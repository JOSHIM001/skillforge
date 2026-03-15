"""
Tests for ai_service.py.

All Groq and OpenAI SDK calls are mocked — no real API keys needed.
Tests verify: caching, fallback behaviour, JSON extraction, hardcoded fallbacks.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.ai_service import (
    _cache_key,
    _extract_json,
    call_ai,
    generate_question,
    grade_answer,
    validate_project_stack,
)

pytestmark = pytest.mark.asyncio


# ── _extract_json ─────────────────────────────────────────────────────────────
class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"key": "value"}') == {"key": "value"}

    def test_markdown_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        assert _extract_json(raw) == {"key": "value"}

    def test_markdown_fence_no_lang(self):
        raw = '```\n{"key": "value"}\n```'
        assert _extract_json(raw) == {"key": "value"}

    def test_json_embedded_in_text(self):
        raw = 'Here is the result: {"score": 0.8} as requested.'
        assert _extract_json(raw) == {"score": 0.8}

    def test_invalid_json_raises(self):
        import json
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not json at all")


# ── call_ai — caching ─────────────────────────────────────────────────────────
class TestCallAiCaching:
    async def test_returns_cached_value(self):
        cached = {"question": "cached question", "difficulty": 2}
        with patch("services.ai_service.cache_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = cached
            result = await call_ai("my prompt", "my system", cache_ttl=60)
        assert result == cached

    async def test_skips_cache_when_ttl_zero(self):
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"score": 0.9}'

        with patch("services.ai_service.cache_get", new_callable=AsyncMock) as mock_get, \
             patch("services.ai_service._call_groq", new_callable=AsyncMock) as mock_groq, \
             patch("services.ai_service.cache_set", new_callable=AsyncMock) as mock_set:

            mock_groq.return_value = {"score": 0.9}
            result = await call_ai("prompt", "system", cache_ttl=0)

        mock_get.assert_not_called()
        mock_set.assert_not_called()
        assert result == {"score": 0.9}

    async def test_caches_successful_response(self):
        with patch("services.ai_service.cache_get", new_callable=AsyncMock) as mock_get, \
             patch("services.ai_service._call_groq", new_callable=AsyncMock) as mock_groq, \
             patch("services.ai_service.cache_set", new_callable=AsyncMock) as mock_set:

            mock_get.return_value = None
            mock_groq.return_value = {"result": "data"}

            await call_ai("prompt", "system", cache_ttl=300)

        mock_set.assert_called_once()
        call_args = mock_set.call_args
        assert call_args[1]["ttl"] == 300


# ── call_ai — fallback ────────────────────────────────────────────────────────
class TestCallAiFallback:
    async def test_falls_back_to_openai_on_groq_failure(self):
        with patch("services.ai_service.cache_get",  new_callable=AsyncMock, return_value=None), \
             patch("services.ai_service.cache_set",  new_callable=AsyncMock), \
             patch("services.ai_service._call_groq", new_callable=AsyncMock, side_effect=RuntimeError("Groq down")), \
             patch("services.ai_service._call_openai", new_callable=AsyncMock, return_value={"fallback": True}):

            result = await call_ai("prompt", "system", cache_ttl=60)

        assert result == {"fallback": True}

    async def test_returns_empty_dict_when_both_fail(self):
        with patch("services.ai_service.cache_get",   new_callable=AsyncMock, return_value=None), \
             patch("services.ai_service.cache_set",   new_callable=AsyncMock), \
             patch("services.ai_service._call_groq",  new_callable=AsyncMock, side_effect=RuntimeError("Groq down")), \
             patch("services.ai_service._call_openai",new_callable=AsyncMock, return_value={}):

            result = await call_ai("prompt", "system", cache_ttl=60)

        assert result == {}

    async def test_does_not_cache_empty_result(self):
        with patch("services.ai_service.cache_get",   new_callable=AsyncMock, return_value=None), \
             patch("services.ai_service.cache_set",   new_callable=AsyncMock) as mock_set, \
             patch("services.ai_service._call_groq",  new_callable=AsyncMock, side_effect=RuntimeError()), \
             patch("services.ai_service._call_openai",new_callable=AsyncMock, return_value={}):

            await call_ai("prompt", "system", cache_ttl=60)

        mock_set.assert_not_called()


# ── generate_question ─────────────────────────────────────────────────────────
class TestGenerateQuestion:
    async def test_returns_ai_response(self):
        ai_result = {
            "question": "Explain Python generators",
            "expected_topics": ["yield", "lazy evaluation"],
            "difficulty": 2,
        }
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value=ai_result):
            result = await generate_question("Python", 2, [])

        assert result["question"] == "Explain Python generators"
        assert "yield" in result["expected_topics"]

    async def test_returns_hardcoded_fallback_on_empty(self):
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            result = await generate_question("Python", 3, [])

        assert "question" in result
        assert "Python" in result["question"]
        assert result["difficulty"] == 3

    async def test_uses_cache_ttl_zero(self):
        """Questions must never be cached."""
        with patch("services.ai_service.call_ai", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = {"question": "q", "expected_topics": [], "difficulty": 1}
            await generate_question("Go", 1, [])

        _, kwargs = mock_call.call_args
        assert kwargs.get("cache_ttl", mock_call.call_args[0][2] if len(mock_call.call_args[0]) > 2 else 1) == 0


# ── grade_answer ──────────────────────────────────────────────────────────────
class TestGradeAnswer:
    async def test_returns_grading(self):
        ai_result = {"score": 0.85, "feedback": "Great answer", "topics_covered": ["yield"]}
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value=ai_result):
            result = await grade_answer("What is a generator?", "A generator uses yield...", ["yield"], "Python")

        assert result["score"] == 0.85
        assert "Great" in result["feedback"]

    async def test_returns_neutral_fallback_on_empty(self):
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            result = await grade_answer("Q", "A", ["topic"], "Python")

        assert result["score"] == 0.5
        assert "topics_covered" in result


# ── validate_project_stack ────────────────────────────────────────────────────
class TestValidateProjectStack:
    async def test_filters_generic_terms_via_caller(self):
        from services.project_service import validate_project, filter_generic

        ai_result = {
            "missing_tech": ["Redis", "authentication", "WebSockets", "testing"],
            "reasoning": "Needs realtime and caching",
        }
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value=ai_result):
            result = await validate_project("A chat app", ["FastAPI"])

        # Generic terms should be removed
        assert "authentication" not in result["missing_tech"]
        assert "testing" not in result["missing_tech"]
        # Specific tools kept
        assert "Redis" in result["missing_tech"]
        assert "WebSockets" in result["missing_tech"]

    async def test_fallback_on_empty(self):
        with patch("services.ai_service.call_ai", new_callable=AsyncMock, return_value={}):
            result = await validate_project_stack("A project", ["React"])

        assert result["missing_tech"] == []
        assert "unavailable" in result["reasoning"].lower()