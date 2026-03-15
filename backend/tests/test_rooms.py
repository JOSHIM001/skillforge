"""
Tests for /rooms/* REST endpoints and WebSocket room behaviour.

WebSocket tests use httpx's websocket client via ASGITransport.
All AI calls mocked throughout.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from models.user import User

pytestmark = pytest.mark.asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────

async def _create_room(client: AsyncClient, headers: dict, name: str = "Test Room") -> dict:
    resp = await client.post("/rooms", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Create room ───────────────────────────────────────────────────────────────

class TestCreateRoom:
    async def test_create_room_success(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            room = await _create_room(client, auth_headers)

        assert "room_id" in room
        assert len(room["room_id"]) == 6          # short code
        assert room["name"] == "Test Room"
        assert room["member_count"] == 1           # creator is auto-member
        assert room["created_by"] == str(test_user.id)

    async def test_create_room_requires_auth(self, client: AsyncClient):
        resp = await client.post("/rooms", json={"name": "Room"})
        assert resp.status_code == 401

    async def test_create_room_empty_name_rejected(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.post("/rooms", json={"name": ""}, headers=auth_headers)
        assert resp.status_code == 422

    async def test_room_codes_are_unique(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            r1 = await _create_room(client, auth_headers, "Room A")
            r2 = await _create_room(client, auth_headers, "Room B")

        assert r1["room_id"] != r2["room_id"]


# ── Get room state ────────────────────────────────────────────────────────────

class TestGetRoomState:
    async def test_get_room_state_success(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            room = await _create_room(client, auth_headers)
            room_id = room["room_id"]

            resp = await client.get(f"/rooms/{room_id}", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["room_id"] == room_id
        assert "members" in data
        assert "team_matrix" in data
        assert "role_gaps" in data
        assert "online_count" in data

    async def test_get_nonexistent_room(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.get("/rooms/ZZZZZZ", headers=auth_headers)
        assert resp.status_code == 404

    async def test_get_room_requires_auth(self, client: AsyncClient):
        resp = await client.get("/rooms/ABCDEF")
        assert resp.status_code == 401


# ── Join room ─────────────────────────────────────────────────────────────────

class TestJoinRoom:
    async def test_join_room_success(
        self,
        client: AsyncClient,
        test_user: User,
        auth_headers: dict,
        db_session,
    ):
        # Create a second user to join
        from models.user import User as UserModel
        from services.auth_service import hash_password, create_access_token

        user2 = UserModel(
            username="user2",
            email="user2@example.com",
            hashed_password=hash_password("password123"),
        )
        db_session.add(user2)
        await db_session.flush()

        token2, _ = create_access_token(user2.id)
        headers2 = {"Authorization": f"Bearer {token2}"}

        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            room = await _create_room(client, auth_headers)
            room_id = room["room_id"]

            # user2 joins
            resp = await client.post(f"/rooms/{room_id}/join", headers=headers2)

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["members"]) == 2

    async def test_join_nonexistent_room(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.post("/rooms/ZZZZZZ/join", headers=auth_headers)
        assert resp.status_code == 404

    async def test_join_is_idempotent(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Joining the same room twice should not error or duplicate membership."""
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            room = await _create_room(client, auth_headers)
            room_id = room["room_id"]

            resp1 = await client.post(f"/rooms/{room_id}/join", headers=auth_headers)
            resp2 = await client.post(f"/rooms/{room_id}/join", headers=auth_headers)

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Should still only have 1 member record
        assert len(resp2.json()["members"]) == 1


# ── Leave room ────────────────────────────────────────────────────────────────

class TestLeaveRoom:
    async def test_leave_room_success(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            room = await _create_room(client, auth_headers)
            room_id = room["room_id"]

            resp = await client.post(f"/rooms/{room_id}/leave", headers=auth_headers)

        assert resp.status_code == 204

    async def test_leave_nonexistent_room(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.post("/rooms/ZZZZZZ/leave", headers=auth_headers)
        assert resp.status_code == 404


# ── Team matrix aggregation ───────────────────────────────────────────────────

class TestTeamMatrix:
    async def test_empty_matrix_for_new_room(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            room = await _create_room(client, auth_headers)
            room_id = room["room_id"]
            resp = await client.get(f"/rooms/{room_id}", headers=auth_headers)

        data = resp.json()
        # New user has no assessed skills → empty team matrix
        assert data["team_matrix"] == {}

    async def test_role_gaps_always_present(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """Role gaps should be returned even with an empty skill matrix."""
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            room = await _create_room(client, auth_headers)
            room_id = room["room_id"]
            resp = await client.get(f"/rooms/{room_id}", headers=auth_headers)

        data = resp.json()
        assert isinstance(data["role_gaps"], list)
        # Local fallback should produce gaps even with no skills
        assert len(data["role_gaps"]) > 0


# ── Local gap analysis unit tests ─────────────────────────────────────────────

class TestLocalGapAnalysis:
    def test_covered_role(self):
        from services.room_service import _local_gap_analysis

        team_matrix = {
            "Python": 0.9,
            "FastAPI": 0.85,
            "PostgreSQL": 0.75,
            "Redis": 0.7,
        }
        gaps = _local_gap_analysis(team_matrix)
        backend = next(g for g in gaps if g.role == "Backend Engineer")
        assert backend.covered is True
        assert backend.coverage_score >= 0.5

    def test_uncovered_role(self):
        from services.room_service import _local_gap_analysis

        # Only backend skills — DevOps should be a gap
        team_matrix = {"Python": 0.9, "FastAPI": 0.85}
        gaps = _local_gap_analysis(team_matrix)
        devops = next(g for g in gaps if g.role == "DevOps Engineer")
        assert devops.covered is False

    def test_all_roles_represented(self):
        from services.room_service import _local_gap_analysis, _ROLE_REQUIREMENTS

        gaps = _local_gap_analysis({})
        gap_roles = {g.role for g in gaps}
        assert gap_roles == set(_ROLE_REQUIREMENTS.keys())


# ── WebSocket tests ───────────────────────────────────────────────────────────

class TestWebSocket:
    async def test_ws_rejects_missing_token(self, client: AsyncClient):
        """WS connection without token should be closed with code 4001."""
        with pytest.raises(Exception):
            async with client.websocket_connect("/ws/room/ABCDEF") as ws:
                pass  # Should not reach here

    async def test_ws_rejects_invalid_token(self, client: AsyncClient):
        with pytest.raises(Exception):
            async with client.websocket_connect(
                "/ws/room/ABCDEF?token=not.a.valid.token"
            ) as ws:
                pass

    async def test_ws_rejects_nonexistent_room(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        from services.auth_service import create_access_token
        token, _ = create_access_token(test_user.id)

        with pytest.raises(Exception):
            async with client.websocket_connect(
                f"/ws/room/ZZZZZZ?token={token}"
            ) as ws:
                pass

    async def test_ws_ping_pong(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        from services.auth_service import create_access_token

        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            room = await _create_room(client, auth_headers)
            room_id = room["room_id"]

        token, _ = create_access_token(test_user.id)

        with patch("services.room_service.build_room_state", new_callable=AsyncMock) as mock_state:
            mock_state.return_value = MagicMock(model_dump=lambda **kw: {
                "room_id": room_id, "name": "Test", "members": [],
                "team_matrix": {}, "role_gaps": [], "online_count": 1,
            })
            async with client.websocket_connect(
                f"/ws/room/{room_id}?token={token}"
            ) as ws:
                # Consume the join broadcast and room_update
                await ws.receive_json()  # join
                await ws.receive_json()  # room_update

                # Send ping
                await ws.send_json({"type": "ping"})
                pong = await ws.receive_json()
                assert pong["type"] == "pong"