"""Tests for the AI processor module.

All Ollama API calls are mocked via requests-mock so no running Ollama
instance is required.
"""

from __future__ import annotations

import json

import pytest
import requests

from processor_ai import AIProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_ollama_response(requests_mock, payload: dict, url: str = "http://localhost:11434/api/generate") -> None:
    """Register a successful Ollama API response with requests_mock."""
    requests_mock.post(
        url,
        json={"model": "llama3", "response": json.dumps(payload), "done": True},
    )


def _sample_emails() -> list[dict]:
    return [
        {
            "id": "msg1",
            "subject": "Interview invitation – please respond by today",
            "sender": "recruiter@techcorp.com",
            "body": "We'd like to invite you for an interview. Please confirm your availability by end of day.",
        },
        {
            "id": "msg2",
            "subject": "School trip permission slip – action required",
            "sender": "admin@primaryschool.edu",
            "body": "Please return the permission slip and payment for the upcoming school trip by Friday.",
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
    def test_urgent_items_returned(self, requests_mock):
        """Items classified as urgent are present in the output."""
        ai_payload = {
            "urgent": ["Interview invite from techcorp.com – confirm availability today"],
            "tasks": [],
            "digest": [],
        }
        _register_ollama_response(requests_mock, ai_payload)

        result = AIProcessor().triage(_sample_emails())

        assert "urgent" in result
        assert len(result["urgent"]) == 1
        assert "Interview" in result["urgent"][0]

    def test_urgent_triggers_api_call(self, requests_mock):
        """Processing emails results in exactly one Ollama API call."""
        _register_ollama_response(requests_mock, {"urgent": ["Critical issue"], "tasks": [], "digest": []})

        AIProcessor().triage(_sample_emails())

        assert requests_mock.call_count == 1


# ---------------------------------------------------------------------------
# Tests: triage – Task category
# ---------------------------------------------------------------------------

class TestTriageTask:
    def test_tasks_returned(self, requests_mock):
        """Action items classified as tasks are present in the output."""
        ai_payload = {
            "urgent": [],
            "tasks": ["Return school trip permission slip and payment by Friday"],
            "digest": [],
        }
        _register_ollama_response(requests_mock, ai_payload)

        result = AIProcessor().triage(_sample_emails())

        assert "tasks" in result
        assert len(result["tasks"]) == 1
        assert "school trip" in result["tasks"][0]

    def test_multiple_tasks_returned(self, requests_mock):
        """Multiple action items are all preserved."""
        ai_payload = {
            "urgent": [],
            "tasks": ["Task one", "Task two", "Task three"],
            "digest": [],
        }
        _register_ollama_response(requests_mock, ai_payload)

        result = AIProcessor().triage(_sample_emails())

        assert len(result["tasks"]) == 3


# ---------------------------------------------------------------------------
# Tests: triage – Digest category
# ---------------------------------------------------------------------------

class TestTriageDigest:
    def test_digest_items_returned(self, requests_mock):
        """Low-priority summaries classified as digest are present in the output."""
        ai_payload = {
            "urgent": [],
            "tasks": [],
            "digest": ["Weekly newsletter: top stories summary"],
        }
        _register_ollama_response(requests_mock, ai_payload)

        result = AIProcessor().triage(_sample_emails())

        assert "digest" in result
        assert len(result["digest"]) == 1
        assert "newsletter" in result["digest"][0]

    def test_junk_not_in_output(self, requests_mock):
        """Junk/irrelevant emails produce empty lists rather than noisy output."""
        _register_ollama_response(requests_mock, {"urgent": [], "tasks": [], "digest": []})

        result = AIProcessor().triage(_sample_emails())

        assert result["urgent"] == []
        assert result["tasks"] == []
        assert result["digest"] == []


# ---------------------------------------------------------------------------
# Tests: triage – combined categories
# ---------------------------------------------------------------------------

class TestTriageCombined:
    def test_all_three_categories_populated(self, requests_mock):
        """All three categories can be populated from a single triage call."""
        ai_payload = {
            "urgent": ["Server down"],
            "tasks": ["Update docs"],
            "digest": ["Newsletter summary"],
        }
        _register_ollama_response(requests_mock, ai_payload)

        result = AIProcessor().triage(_sample_emails())

        assert len(result["urgent"]) == 1
        assert len(result["tasks"]) == 1
        assert len(result["digest"]) == 1

    def test_empty_email_list_returns_empty_categories(self):
        """An empty inbox short-circuits before any API call and returns empty lists."""
        result = AIProcessor().triage([])

        assert result == {"urgent": [], "tasks": [], "digest": []}


# ---------------------------------------------------------------------------
# Tests: API request payload
# ---------------------------------------------------------------------------

class TestApiRequestPayload:
    def test_correct_model_sent(self, requests_mock):
        """The configured model name is included in the request payload."""
        _register_ollama_response(requests_mock, {"urgent": [], "tasks": [], "digest": []})

        AIProcessor(model="llama3").triage(_sample_emails())

        body = requests_mock.last_request.json()
        assert body["model"] == "llama3"

    def test_correct_url_called(self, requests_mock):
        """The Ollama generate endpoint is the target of the POST request."""
        _register_ollama_response(requests_mock, {"urgent": [], "tasks": [], "digest": []})

        AIProcessor().triage(_sample_emails())

        assert requests_mock.last_request.url == "http://localhost:11434/api/generate"

    def test_prompt_contains_email_content(self, requests_mock):
        """The prompt sent to the model includes content from the emails."""
        _register_ollama_response(requests_mock, {"urgent": [], "tasks": [], "digest": []})

        AIProcessor().triage(_sample_emails())

        body = requests_mock.last_request.json()
        prompt = body["prompt"]
        assert "Interview invitation" in prompt
        assert "recruiter@techcorp.com" in prompt


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_malformed_json_response_raises_value_error(self, requests_mock):
        """A non-JSON response from the model raises a ValueError."""
        requests_mock.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3", "response": "not valid json", "done": True},
        )

        with pytest.raises(ValueError, match="Failed to parse"):
            AIProcessor().triage(_sample_emails())

    def test_http_error_propagates(self, requests_mock):
        """An HTTP error from the Ollama API propagates to the caller."""
        requests_mock.post(
            "http://localhost:11434/api/generate",
            status_code=503,
        )

        with pytest.raises(requests.HTTPError):
            AIProcessor().triage(_sample_emails())

    def test_non_string_list_items_coerced_to_str(self, requests_mock):
        """Non-string items in triage lists (e.g. dicts) are coerced to strings."""
        requests_mock.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3", "response": '{"urgent": [{"key": "val"}], "tasks": [], "digest": []}', "done": True},
        )

        result = AIProcessor().triage(_sample_emails())

        assert isinstance(result["urgent"][0], str)
