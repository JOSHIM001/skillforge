"""
routers/auth.py — fixed github_callback Response injection.

Change from original: `response: Response = None` → `response: Response`
FastAPI requires Response to be a proper dependency with no default.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from middleware.auth_middleware import CurrentUser
from models.user import User
from schemas.auth import (
    LoginRequest,
    RefreshResponse,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from services.auth_service import (
    create_access_token,
    exchange_github_code,
    fetch_github_profile,
    get_or_create_github_user,
    get_user_by_email,
    get_user_by_username,
    hash_password,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_password,
)

router = APIRouter()

_REFRESH_COOKIE = "refresh_token"
_oauth_states: set[str] = set()


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=raw_token,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 86400,
        path="/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=_REFRESH_COOKIE, path="/auth")


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)) -> TokenPair:
    if await get_user_by_email(body.email, db):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    if await get_user_by_username(body.username, db):
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
    user = User(username=body.username, email=body.email, hashed_password=hash_password(body.password))
    db.add(user)
    await db.flush()
    access_token, expires_in = create_access_token(user.id)
    raw_refresh = await issue_refresh_token(user.id, db)
    _set_refresh_cookie(response, raw_refresh)
    return TokenPair(access_token=access_token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> TokenPair:
    user = await get_user_by_email(body.email, db)
    dummy_hash = "$2b$12$KIXbF1kWqL8qv8t1tHZ8RO"
    password_ok = verify_password(body.password, user.hashed_password if (user and user.hashed_password) else dummy_hash)
    if not user or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    access_token, expires_in = create_access_token(user.id)
    raw_refresh = await issue_refresh_token(user.id, db)
    _set_refresh_cookie(response, raw_refresh)
    return TokenPair(access_token=access_token, expires_in=expires_in, user=UserOut.model_validate(user))


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> RefreshResponse:
    raw_token = request.cookies.get(_REFRESH_COOKIE)
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token provided")
    try:
        new_raw, user_id = await rotate_refresh_token(raw_token, db)
    except ValueError as exc:
        _clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))
    access_token, expires_in = create_access_token(user_id)
    _set_refresh_cookie(response, new_raw)
    return RefreshResponse(access_token=access_token, expires_in=expires_in)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> None:
    raw_token = request.cookies.get(_REFRESH_COOKIE)
    if raw_token:
        await revoke_refresh_token(raw_token, db)
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUser) -> UserOut:
    return UserOut.model_validate(current_user)


@router.get("/github/login")
async def github_login() -> Response:
    state = secrets.token_hex(16)
    _oauth_states.add(state)
    github_url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={settings.frontend_url}/auth/github/callback"
        f"&scope=read:user,user:email"
        f"&state={state}"
    )
    return Response(status_code=status.HTTP_302_FOUND, headers={"Location": github_url})


@router.get("/github/callback", response_model=TokenPair)
async def github_callback(
    response: Response,           # ← FIX: proper FastAPI injection, no default=None
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> TokenPair:
    if state not in _oauth_states:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid OAuth state parameter")
    _oauth_states.discard(state)
    try:
        gh_access_token = await exchange_github_code(code)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"GitHub token exchange failed: {exc}")
    try:
        profile = await fetch_github_profile(gh_access_token)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"GitHub profile fetch failed: {exc}")
    user = await get_or_create_github_user(profile, db)
    access_token, expires_in = create_access_token(user.id)
    raw_refresh = await issue_refresh_token(user.id, db)
    _set_refresh_cookie(response, raw_refresh)
    return TokenPair(access_token=access_token, expires_in=expires_in, user=UserOut.model_validate(user))