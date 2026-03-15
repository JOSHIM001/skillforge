import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User


pytestmark = pytest.mark.asyncio


class TestRegister:
    async def test_register_success(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "username": "newuser",
            "email": "new@example.com",
            "password": "securepass123",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data
        assert data["user"]["username"] == "newuser"
        assert data["user"]["email"] == "new@example.com"
        # Refresh token cookie should be set
        assert "refresh_token" in resp.cookies

    async def test_register_duplicate_email(self, client: AsyncClient, test_user: User):
        resp = await client.post("/auth/register", json={
            "username": "different",
            "email": test_user.email,
            "password": "password123",
        })
        assert resp.status_code == 409
        assert "Email" in resp.json()["detail"]

    async def test_register_duplicate_username(self, client: AsyncClient, test_user: User):
        resp = await client.post("/auth/register", json={
            "username": test_user.username,
            "email": "unique@example.com",
            "password": "password123",
        })
        assert resp.status_code == 409
        assert "Username" in resp.json()["detail"]

    async def test_register_weak_password(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "username": "someone",
            "email": "someone@example.com",
            "password": "short",   # < 8 chars
        })
        assert resp.status_code == 422

    async def test_register_invalid_username_chars(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "username": "bad username!",
            "email": "ok@example.com",
            "password": "password123",
        })
        assert resp.status_code == 422

    async def test_register_reserved_username(self, client: AsyncClient):
        resp = await client.post("/auth/register", json={
            "username": "admin",
            "email": "admin@example.com",
            "password": "password123",
        })
        assert resp.status_code == 422


class TestLogin:
    async def test_login_success(self, client: AsyncClient, test_user: User):
        resp = await client.post("/auth/login", json={
            "email": test_user.email,
            "password": "password123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["id"] == str(test_user.id)

    async def test_login_wrong_password(self, client: AsyncClient, test_user: User):
        resp = await client.post("/auth/login", json={
            "email": test_user.email,
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    async def test_login_unknown_email(self, client: AsyncClient):
        resp = await client.post("/auth/login", json={
            "email": "ghost@example.com",
            "password": "password123",
        })
        assert resp.status_code == 401

    async def test_login_sets_cookie(self, client: AsyncClient, test_user: User):
        resp = await client.post("/auth/login", json={
            "email": test_user.email,
            "password": "password123",
        })
        assert "refresh_token" in resp.cookies


class TestRefresh:
    async def test_refresh_success(self, client: AsyncClient, test_user: User):
        # Login to get a refresh cookie
        login_resp = await client.post("/auth/login", json={
            "email": test_user.email,
            "password": "password123",
        })
        assert login_resp.status_code == 200

        # Use the refresh cookie to get a new access token
        resp = await client.post("/auth/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        # New refresh cookie should be set (rotation)
        assert "refresh_token" in resp.cookies

    async def test_refresh_without_cookie(self, client: AsyncClient):
        resp = await client.post("/auth/refresh")
        assert resp.status_code == 401


class TestLogout:
    async def test_logout_clears_cookie(self, client: AsyncClient, test_user: User):
        await client.post("/auth/login", json={
            "email": test_user.email,
            "password": "password123",
        })
        resp = await client.post("/auth/logout")
        assert resp.status_code == 204
        # Refresh cookie should be cleared
        assert resp.cookies.get("refresh_token") in (None, "")


class TestMe:
    async def test_me_authenticated(self, client: AsyncClient, test_user: User, auth_headers: dict):
        resp = await client.get("/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(test_user.id)
        assert data["username"] == test_user.username

    async def test_me_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/auth/me")
        assert resp.status_code == 401

    async def test_me_invalid_token(self, client: AsyncClient):
        resp = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"})
        assert resp.status_code == 401