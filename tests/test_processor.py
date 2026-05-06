"""Tests for the AI processor module.

All Ollama API calls are mocked so no running Ollama instance is required.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from processor_ai import AIProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ollama_response(payload: dict) -> MagicMock:
    """Build a mock requests.Response that returns *payload* as the Ollama JSON."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "model": "llama3",
        "response": json.dumps(payload),
        "done": True,
    }
    return mock_resp


def _sample_emails() -> list[dict]:
    return [
        {
            "id": "msg1",
            "subject": "URGENT: Server is down",
            "sender": "ops@example.com",
            "body": "Production is down, need a response immediately.",
        },
        {
            "id": "msg2",
            "subject": "Task: Update documentation",
            "sender": "manager@example.com",
            "body": "Please update the API docs by end of week.",
        },
        {
            "id": "msg3",
            "subject": "Weekly newsletter",
            "sender": "news@example.com",
            "body": "Here are the top stories this week...",
        },
    ]


# ---------------------------------------------------------------------------
# Tests: AIProcessor initialisation
# ---------------------------------------------------------------------------

class TestAIProcessorInit:
    def test_default_url(self):
        processor = AIProcessor()
        assert processor.url == "http://localhost:11434/api/generate"

    def test_custom_url(self):
        processor = AIProcessor(url="http://custom:11434/api/generate")
        assert processor.url == "http://custom:11434/api/generate"

    def test_default_model(self):
        processor = AIProcessor()
        assert processor.model == "llama3"

    def test_custom_model(self):
        processor = AIProcessor(model="mistral")
        assert processor.model == "mistral"


# ---------------------------------------------------------------------------
# Tests: triage – Urgent category
# ---------------------------------------------------------------------------

class TestTriageUrgent:
    def test_urgent_items_returned(self):
        """Items classified as urgent are present in the output."""
        ai_payload = {
            "urgent": ["Server is down – reply immediately"],
            "tasks": [],
            "digest": [],
        }
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp) as mock_post:
            processor = AIProcessor()
            result = processor.triage(_sample_emails())

        assert "urgent" in result
        assert len(result["urgent"]) == 1
        assert "Server is down" in result["urgent"][0]

    def test_urgent_triggers_api_call(self):
        """Processing emails results in exactly one Ollama API call."""
        ai_payload = {"urgent": ["Critical issue"], "tasks": [], "digest": []}
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp) as mock_post:
            processor = AIProcessor()
            processor.triage(_sample_emails())

        mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: triage – Task category
# ---------------------------------------------------------------------------

class TestTriageTask:
    def test_tasks_returned(self):
        """Action items classified as tasks are present in the output."""
        ai_payload = {
            "urgent": [],
            "tasks": ["Update the API docs by end of week"],
            "digest": [],
        }
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp):
            processor = AIProcessor()
            result = processor.triage(_sample_emails())

        assert "tasks" in result
        assert len(result["tasks"]) == 1
        assert "API docs" in result["tasks"][0]

    def test_multiple_tasks_returned(self):
        """Multiple action items are all preserved."""
        ai_payload = {
            "urgent": [],
            "tasks": ["Task one", "Task two", "Task three"],
            "digest": [],
        }
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp):
            processor = AIProcessor()
            result = processor.triage(_sample_emails())

        assert len(result["tasks"]) == 3


# ---------------------------------------------------------------------------
# Tests: triage – Digest category
# ---------------------------------------------------------------------------

class TestTriageDigest:
    def test_digest_items_returned(self):
        """Low-priority summaries classified as digest are present in the output."""
        ai_payload = {
            "urgent": [],
            "tasks": [],
            "digest": ["Weekly newsletter: top stories summary"],
        }
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp):
            processor = AIProcessor()
            result = processor.triage(_sample_emails())

        assert "digest" in result
        assert len(result["digest"]) == 1
        assert "newsletter" in result["digest"][0]

    def test_junk_not_in_output(self):
        """Junk/irrelevant emails produce empty lists rather than noisy output."""
        ai_payload = {"urgent": [], "tasks": [], "digest": []}
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp):
            processor = AIProcessor()
            result = processor.triage(_sample_emails())

        assert result["urgent"] == []
        assert result["tasks"] == []
        assert result["digest"] == []


# ---------------------------------------------------------------------------
# Tests: triage – combined categories
# ---------------------------------------------------------------------------

class TestTriageCombined:
    def test_all_three_categories_populated(self):
        """All three categories can be populated from a single triage call."""
        ai_payload = {
            "urgent": ["Server down"],
            "tasks": ["Update docs"],
            "digest": ["Newsletter summary"],
        }
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp):
            processor = AIProcessor()
            result = processor.triage(_sample_emails())

        assert len(result["urgent"]) == 1
        assert len(result["tasks"]) == 1
        assert len(result["digest"]) == 1

    def test_empty_email_list_returns_empty_categories(self):
        """An empty inbox returns empty lists for all categories."""
        ai_payload = {"urgent": [], "tasks": [], "digest": []}
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp):
            processor = AIProcessor()
            result = processor.triage([])

        assert result == {"urgent": [], "tasks": [], "digest": []}


# ---------------------------------------------------------------------------
# Tests: API request payload
# ---------------------------------------------------------------------------

class TestApiRequestPayload:
    def test_correct_model_sent(self):
        """The configured model name is included in the request payload."""
        ai_payload = {"urgent": [], "tasks": [], "digest": []}
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp) as mock_post:
            processor = AIProcessor(model="llama3")
            processor.triage(_sample_emails())

        body = mock_post.call_args.kwargs["json"]
        assert body["model"] == "llama3"

    def test_correct_url_called(self):
        """The Ollama generate endpoint is the target of the POST request."""
        ai_payload = {"urgent": [], "tasks": [], "digest": []}
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp) as mock_post:
            processor = AIProcessor()
            processor.triage(_sample_emails())

        called_url = mock_post.call_args[0][0]
        assert called_url == "http://localhost:11434/api/generate"

    def test_prompt_contains_email_content(self):
        """The prompt sent to the model includes content from the emails."""
        ai_payload = {"urgent": [], "tasks": [], "digest": []}
        mock_resp = _make_ollama_response(ai_payload)

        with patch("processor_ai.requests.post", return_value=mock_resp) as mock_post:
            processor = AIProcessor()
            processor.triage(_sample_emails())

        body = mock_post.call_args.kwargs["json"]
        prompt = body["prompt"]
        assert "URGENT: Server is down" in prompt
        assert "ops@example.com" in prompt


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_malformed_json_response_raises_value_error(self):
        """A non-JSON response from the model raises a ValueError."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"model": "llama3", "response": "not valid json", "done": True}

        with patch("processor_ai.requests.post", return_value=mock_resp):
            processor = AIProcessor()
            with pytest.raises(ValueError, match="Failed to parse"):
                processor.triage(_sample_emails())

    def test_http_error_propagates(self):
        """An HTTP error from the Ollama API propagates to the caller."""
        import requests as req_lib

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req_lib.HTTPError("503 Service Unavailable")

        with patch("processor_ai.requests.post", return_value=mock_resp):
            processor = AIProcessor()
            with pytest.raises(req_lib.HTTPError):
                processor.triage(_sample_emails())
