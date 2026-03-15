import secrets
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.user import RefreshToken, User
from schemas.auth import GitHubProfile

# ── Password hashing ──────────────────────────────────────────────────────────
import bcrypt

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

# ── JWT ───────────────────────────────────────────────────────────────────────
def create_access_token(user_id: uuid.UUID) -> tuple[str, int]:
    """
    Returns (encoded_jwt, expires_in_seconds).
    Short-lived: 15 minutes by default.
    """
    expires_in = settings.access_token_expire_minutes * 60
    expire = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    return token, expires_in


def decode_access_token(token: str) -> uuid.UUID:
    """
    Decode and validate an access JWT.
    Raises JWTError (from python-jose) on any failure — callers
    should catch this and return HTTP 401.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        raise

    if payload.get("type") != "access":
        raise JWTError("Not an access token")

    sub = payload.get("sub")
    if not sub:
        raise JWTError("Missing subject claim")

    try:
        return uuid.UUID(sub)
    except ValueError:
        raise JWTError("Invalid subject UUID")


# ── Refresh tokens ────────────────────────────────────────────────────────────
def _generate_raw_refresh_token() -> str:
    """Cryptographically secure random token (32 bytes = 64 hex chars)."""
    return secrets.token_hex(32)


async def issue_refresh_token(user_id: uuid.UUID, db: AsyncSession) -> str:
    """
    Generate a new refresh token, persist its hash, and return the raw token.
    The raw token is placed in an httpOnly cookie — it never touches the DB.
    """
    raw = _generate_raw_refresh_token()
    token_hash = RefreshToken.hash_token(raw)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)

    db_token = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(db_token)
    await db.flush()   # write to DB within the current transaction
    return raw


async def rotate_refresh_token(
    raw_token: str,
    db: AsyncSession,
) -> tuple[str, uuid.UUID]:
    """
    Validate the incoming refresh token, revoke it, issue a new one.
    Returns (new_raw_token, user_id).

    Raises ValueError with a descriptive message on any failure so the
    router can return a clean 401.
    """
    token_hash = RefreshToken.hash_token(raw_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    db_token = result.scalar_one_or_none()

    if db_token is None:
        raise ValueError("Refresh token not found")
    if db_token.revoked:
        # Possible token theft — revoke ALL tokens for this user as a precaution
        await _revoke_all_user_tokens(db_token.user_id, db)
        raise ValueError("Refresh token already revoked — possible replay attack")
    if db_token.is_expired:
        raise ValueError("Refresh token has expired")

    # Revoke the used token (rotation: one token per use)
    db_token.revoked = True
    await db.flush()

    # Issue replacement
    new_raw = await issue_refresh_token(db_token.user_id, db)
    return new_raw, db_token.user_id


async def revoke_refresh_token(raw_token: str, db: AsyncSession) -> None:
    """Revoke a specific refresh token (called on logout)."""
    token_hash = RefreshToken.hash_token(raw_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    db_token = result.scalar_one_or_none()
    if db_token:
        db_token.revoked = True
        await db.flush()


async def _revoke_all_user_tokens(user_id: uuid.UUID, db: AsyncSession) -> None:
    """Revoke every active refresh token for a user. Called on suspected replay attack."""
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked == False,  # noqa: E712
        )
    )
    for token in result.scalars().all():
        token.revoked = True
    await db.flush()


# ── User lookup helpers ───────────────────────────────────────────────────────
async def get_user_by_email(email: str, db: AsyncSession) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(user_id: uuid.UUID, db: AsyncSession) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_github_id(github_id: int, db: AsyncSession) -> User | None:
    result = await db.execute(select(User).where(User.github_id == github_id))
    return result.scalar_one_or_none()


async def get_user_by_username(username: str, db: AsyncSession) -> User | None:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


# ── GitHub OAuth ──────────────────────────────────────────────────────────────
async def exchange_github_code(code: str) -> str:
    """
    Exchange a GitHub OAuth authorization code for an access token.
    Raises httpx.HTTPError on network failure.
    Raises ValueError if GitHub returns an error field.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id":     settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code":          code,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    if "error" in data:
        raise ValueError(f"GitHub OAuth error: {data.get('error_description', data['error'])}")

    return data["access_token"]


async def fetch_github_profile(access_token: str) -> GitHubProfile:
    """
    Fetch the authenticated GitHub user's profile.
    Also attempts a separate /user/emails call if the primary email is private.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://api.github.com/user", headers=headers)
        resp.raise_for_status()
        data = resp.json()

        email = data.get("email")

        # GitHub may hide the primary email — fetch from /user/emails
        if not email:
            emails_resp = await client.get(
                "https://api.github.com/user/emails", headers=headers
            )
            if emails_resp.is_success:
                for entry in emails_resp.json():
                    if entry.get("primary") and entry.get("verified"):
                        email = entry["email"]
                        break

    return GitHubProfile(
        id=data["id"],
        login=data["login"],
        email=email,
        avatar_url=data.get("avatar_url"),
        name=data.get("name"),
    )


async def get_or_create_github_user(profile: GitHubProfile, db: AsyncSession) -> User:
    """
    Find existing user by GitHub ID, or create a new one.
    If a user exists with the same email (registered manually before),
    link their GitHub ID to the existing account.
    """
    # 1. Check by GitHub ID (returning user via OAuth)
    user = await get_user_by_github_id(profile.id, db)
    if user:
        # Refresh avatar in case it changed
        user.avatar_url = profile.avatar_url
        await db.flush()
        return user

    # 2. Check by email (link GitHub to existing account)
    if profile.email:
        user = await get_user_by_email(profile.email, db)
        if user:
            user.github_id = profile.id
            user.avatar_url = profile.avatar_url or user.avatar_url
            await db.flush()
            return user

    # 3. Create brand new user
    username = await _unique_username(profile.login, db)
    user = User(
        github_id=profile.id,
        username=username,
        email=profile.email or f"github_{profile.id}@placeholder.local",
        avatar_url=profile.avatar_url,
        hashed_password=None,  # GitHub-only — no password
    )
    db.add(user)
    await db.flush()
    return user


async def _unique_username(base: str, db: AsyncSession) -> str:
    """
    Ensure a username is unique. Appends a numeric suffix if taken.
    e.g. "johndoe" → "johndoe_2" → "johndoe_3"
    """
    # Sanitize: keep only alphanumeric, hyphens, underscores
    import re
    clean = re.sub(r"[^a-zA-Z0-9_-]", "", base)[:90] or "user"

    candidate = clean
    suffix = 2
    while await get_user_by_username(candidate, db):
        candidate = f"{clean}_{suffix}"
        suffix += 1
    return candidate