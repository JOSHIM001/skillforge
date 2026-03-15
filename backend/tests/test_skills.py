"""
Tests for /skills/* endpoints.

All AI calls mocked — tests run without Groq/OpenAI keys.
Covers: matrix, session lifecycle, question generation,
answer submission, auto-complete, task polling.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from models.user import User

pytestmark = pytest.mark.asyncio

# ── Shared mock AI responses ──────────────────────────────────────────────────
MOCK_QUESTION = {
    "question": "Explain Python generators and their memory advantages.",
    "expected_topics": ["yield", "lazy evaluation", "memory efficiency"],
    "difficulty": 2,
}

MOCK_GRADE = {
    "score": 0.82,
    "feedback": "Good answer. You correctly explained yield and lazy evaluation.",
    "topics_covered": ["yield", "lazy evaluation"],
}


# ── Matrix ────────────────────────────────────────────────────────────────────
class TestSkillMatrix:
    async def test_empty_matrix_for_new_user(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.get("/skills/matrix", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"] == []
        assert data["matrix"] == {}
        assert data["total_assessed"] == 0
        assert data["overall_score"] == 0.0

    async def test_matrix_requires_auth(self, client: AsyncClient):
        resp = await client.get("/skills/matrix")
        assert resp.status_code == 401

    async def test_matrix_populated_after_session(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        with patch("services.ai_service.call_ai", new_callable=AsyncMock) as mock_ai:
            mock_ai.return_value = MOCK_QUESTION

            # Start session
            start = await client.post(
                "/skills/session/start",
                json={"skill_name": "Python"},
                headers=auth_headers,
            )
            session_id = start.json()["session_id"]

            # Get question
            await client.get(f"/skills/session/{session_id}/question", headers=auth_headers)

            # Submit answer (grade mock)
            mock_ai.return_value = MOCK_GRADE
            await client.post(
                f"/skills/session/{session_id}/answer",
                json={
                    "question_text": MOCK_QUESTION["question"],
                    "expected_topics": MOCK_QUESTION["expected_topics"],
                    "user_answer": "A generator uses yield to produce values lazily, avoiding loading everything into memory at once.",
                    "difficulty": 2,
                },
                headers=auth_headers,
            )

        resp = await client.get("/skills/matrix", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "Python" in data["matrix"]
        assert data["total_assessed"] == 1


# ── Session start ─────────────────────────────────────────────────────────────
class TestSessionStart:
    async def test_start_session_success(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.post(
            "/skills/session/start",
            json={"skill_name": "JavaScript"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["skill_name"] == "JavaScript"
        assert data["status"] == "active"
        assert data["questions_asked"] == 0
        assert "session_id" in data

    async def test_start_session_normalises_skill_name(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.post(
            "/skills/session/start",
            json={"skill_name": "  python  "},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["skill_name"] == "python"  # stripped

    async def test_start_session_requires_auth(self, client: AsyncClient):
        resp = await client.post(
            "/skills/session/start", json={"skill_name": "Python"}
        )
        assert resp.status_code == 401

    async def test_start_session_empty_name_rejected(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.post(
            "/skills/session/start",
            json={"skill_name": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_starting_new_session_abandons_previous(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        # Start first session
        r1 = await client.post(
            "/skills/session/start",
            json={"skill_name": "Go"},
            headers=auth_headers,
        )
        s1_id = r1.json()["session_id"]

        # Start second session for same skill
        await client.post(
            "/skills/session/start",
            json={"skill_name": "Go"},
            headers=auth_headers,
        )

        # First session should now be abandoned
        r1_detail = await client.get(f"/skills/session/{s1_id}", headers=auth_headers)
        assert r1_detail.json()["status"] == "abandoned"


# ── Question generation ───────────────────────────────────────────────────────
class TestGetQuestion:
    async def _start(self, client, auth_headers, skill="Python") -> str:
        r = await client.post(
            "/skills/session/start",
            json={"skill_name": skill},
            headers=auth_headers,
        )
        return r.json()["session_id"]

    async def test_get_question_success(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        session_id = await self._start(client, auth_headers)
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value=MOCK_QUESTION):
            resp = await client.get(
                f"/skills/session/{session_id}/question", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "question" in data
        assert data["difficulty"] in range(1, 6)
        assert data["questions_asked"] == 0
        assert data["questions_remaining"] == 8
        # expected_topics must NOT be in the response (anti-cheat)
        assert "expected_topics" not in data

    async def test_get_question_wrong_session_returns_404(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.get(
            f"/skills/session/{uuid.uuid4()}/question", headers=auth_headers
        )
        assert resp.status_code == 404

    async def test_get_question_requires_auth(self, client: AsyncClient):
        resp = await client.get(f"/skills/session/{uuid.uuid4()}/question")
        assert resp.status_code == 401


# ── Answer submission ─────────────────────────────────────────────────────────
class TestSubmitAnswer:
    async def _setup_session_with_question(self, client, auth_headers):
        """Start a session and get a question, return (session_id)."""
        r = await client.post(
            "/skills/session/start",
            json={"skill_name": "TypeScript"},
            headers=auth_headers,
        )
        session_id = r.json()["session_id"]

        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value=MOCK_QUESTION):
            await client.get(f"/skills/session/{session_id}/question", headers=auth_headers)

        return session_id

    async def test_submit_answer_success(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        session_id = await self._setup_session_with_question(client, auth_headers)

        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value=MOCK_GRADE):
            resp = await client.post(
                f"/skills/session/{session_id}/answer",
                json={
                    "question_text": MOCK_QUESTION["question"],
                    "expected_topics": MOCK_QUESTION["expected_topics"],
                    "user_answer": "Generators use yield keyword to produce values one at a time, conserving memory by not loading the entire sequence.",
                    "difficulty": 2,
                },
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert 0.0 <= data["score"] <= 1.0
        assert "feedback" in data
        assert isinstance(data["topics_covered"], list)
        assert 0.0 <= data["new_skill_score"] <= 1.0
        assert data["questions_asked"] == 1

    async def test_short_answer_rejected(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        session_id = await self._setup_session_with_question(client, auth_headers)
        resp = await client.post(
            f"/skills/session/{session_id}/answer",
            json={
                "question_text": "Q",
                "expected_topics": [],
                "user_answer": "idk",    # too short
                "difficulty": 1,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_session_auto_completes_at_max_questions(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        """After 8 answers the session should be marked completed."""
        r = await client.post(
            "/skills/session/start",
            json={"skill_name": "Rust"},
            headers=auth_headers,
        )
        session_id = r.json()["session_id"]

        for i in range(8):
            with patch("services.ai_service.call_ai", new_callable=AsyncMock) as mock:
                mock.return_value = MOCK_QUESTION
                await client.get(f"/skills/session/{session_id}/question", headers=auth_headers)

                mock.return_value = MOCK_GRADE
                resp = await client.post(
                    f"/skills/session/{session_id}/answer",
                    json={
                        "question_text": "Q",
                        "expected_topics": [],
                        "user_answer": "A detailed and thoughtful answer about the topic in question.",
                        "difficulty": 2,
                    },
                    headers=auth_headers,
                )

        # Last response should mark session complete
        assert resp.json()["session_complete"] is True

        # Session detail should show completed status
        detail = await client.get(f"/skills/session/{session_id}", headers=auth_headers)
        assert detail.json()["status"] == "completed"


# ── Manual completion ─────────────────────────────────────────────────────────
class TestCompleteSession:
    async def test_manual_complete(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        r = await client.post(
            "/skills/session/start",
            json={"skill_name": "Kotlin"},
            headers=auth_headers,
        )
        session_id = r.json()["session_id"]

        resp = await client.post(
            f"/skills/session/{session_id}/complete", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    async def test_complete_nonexistent_session(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.post(
            f"/skills/session/{uuid.uuid4()}/complete", headers=auth_headers
        )
        assert resp.status_code == 404


# ── Spaced repetition queue ───────────────────────────────────────────────────
class TestSpacedQueue:
    async def test_empty_queue_for_new_user(
        self, client: AsyncClient, test_user: User, auth_headers: dict
    ):
        resp = await client.get("/skills/matrix/spaced-queue", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["items"] == []


# ── Task polling ──────────────────────────────────────────────────────────────
class TestTaskPolling:
    async def test_pending_task(self, client: AsyncClient, auth_headers: dict):
        with patch("redis_client.cache_get", new_callable=AsyncMock, return_value=None):
            resp = await client.get("/skills/tasks/some-task-id")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    async def test_done_task(self, client: AsyncClient, auth_headers: dict):
        result_payload = {"status": "done", "result": {"score": 0.9, "feedback": "Excellent"}}
        with patch("redis_client.cache_get", new_callable=AsyncMock, return_value=result_payload):
            resp = await client.get("/skills/tasks/finished-task-id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "done"
        assert data["result"]["score"] == 0.9

    async def test_error_task(self, client: AsyncClient, auth_headers: dict):
        error_payload = {"status": "error", "detail": "Groq API unavailable"}
        with patch("redis_client.cache_get", new_callable=AsyncMock, return_value=error_payload):
            resp = await client.get("/skills/tasks/failed-task-id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "Groq" in data["detail"]