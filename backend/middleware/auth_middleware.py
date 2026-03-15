import uuid
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.auth_service import decode_access_token, get_user_by_id
from models.user import User

# ── Token extractor ───────────────────────────────────────────────────────────
async def _get_token_from_header(
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    """Extract raw JWT from 'Authorization: Bearer <token>' header."""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


# ── Core dependency ───────────────────────────────────────────────────────────
async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    token: Annotated[str | None, Depends(_get_token_from_header)],
) -> User:
    """
    FastAPI dependency. Decodes the JWT, loads the user from DB.

    Usage in any protected router:
        @router.get("/me")
        async def me(user: CurrentUser):
            return user
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

    try:
        user_id: uuid.UUID = decode_access_token(token)
    except JWTError:
        raise credentials_exception

    user = await get_user_by_id(user_id, db)
    if user is None:
        raise credentials_exception

    return user


# ── Convenience type alias (use this in route signatures) ─────────────────────
CurrentUser = Annotated[User, Depends(get_current_user)]


# ── Optional auth (for routes that work with or without auth) ─────────────────
async def get_current_user_optional(
    db: Annotated[AsyncSession, Depends(get_db)],
    token: Annotated[str | None, Depends(_get_token_from_header)],
) -> User | None:
    """
    Like get_current_user but returns None instead of raising 401.
    Useful for routes that serve both guests and authenticated users.
    """
    if not token:
        return None
    try:
        user_id = decode_access_token(token)
        return await get_user_by_id(user_id, db)
    except (JWTError, Exception):
        return None


OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
