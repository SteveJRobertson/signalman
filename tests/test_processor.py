"""Tests for the AI processor module.

All Ollama API calls are mocked via requests-mock so no running Ollama
instance is required.
"""

from __future__ import annotations

import json

import pytest
import requests

from config import Settings
from processor_ai import AIProcessor, ClassificationError, TriagedItem, TriageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**overrides) -> Settings:
    """Build a Settings instance with sane test defaults."""
    base = {
        "signal_sender": "+10000000001",
        "signal_recipient": "+10000000002",
    }
    base.update(overrides)
    return Settings(**base)


URL = "http://localhost:11434/api/generate"


def _reply(category: str, summary: str = "A summary") -> dict:
    """An Ollama /api/generate body whose model reply is a valid verdict."""
    return {"model": "llama3", "response": json.dumps({"category": category, "summary": summary}), "done": True}


def _raw_reply(text: str) -> dict:
    """An Ollama /api/generate body carrying arbitrary (possibly bad) model text."""
    return {"model": "llama3", "response": text, "done": True}


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
    def test_default_generate_url(self):
        assert AIProcessor(_settings()).generate_url == "http://localhost:11434/api/generate"

    def test_custom_generate_url(self):
        processor = AIProcessor(_settings(ollama_url="http://custom:11434"))
        assert processor.generate_url == "http://custom:11434/api/generate"

    def test_default_model(self):
        assert AIProcessor(_settings()).model == "llama3"

    def test_custom_model(self):
        assert AIProcessor(_settings(ollama_model="mistral")).model == "mistral"


# ---------------------------------------------------------------------------
# Tests: TriageResult
# ---------------------------------------------------------------------------

def _item(category: str, summary: str = "s", email_id: str = "id") -> TriagedItem:
    return TriagedItem(email_id=email_id, sender="a@b.c", subject="subj", category=category, summary=summary)


class TestTriageResult:
    def test_defaults_are_empty(self):
        result = TriageResult()

        assert result.items == () and result.failed_ids == () and result.skipped_count == 0

    def test_by_category_filters_and_preserves_order(self):
        result = TriageResult(items=(_item("urgent", "a"), _item("tasks", "b"), _item("urgent", "c")))

        assert [i.summary for i in result.by_category("urgent")] == ["a", "c"]
        assert [i.summary for i in result.by_category("tasks")] == ["b"]
        assert result.by_category("digest") == []

    def test_is_immutable(self):
        with pytest.raises(AttributeError):
            TriageResult().skipped_count = 3  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests: triage – one call per email, verdict mapped back to its email
# ---------------------------------------------------------------------------

class TestTriage:
    def test_empty_email_list_returns_empty_result_without_api_call(self, requests_mock):
        result = AIProcessor(_settings()).triage([])

        assert result == TriageResult()
        assert requests_mock.call_count == 0

    @pytest.mark.parametrize("category", ["urgent", "tasks", "digest", "ignore"])
    def test_each_category_is_returned(self, requests_mock, category):
        requests_mock.post(URL, json=_reply(category, "Do the thing"))

        result = AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert [i.category for i in result.items] == [category]
        assert result.items[0].summary == "Do the thing"

    def test_one_api_call_per_email(self, requests_mock):
        requests_mock.post(URL, json=_reply("digest"))
        emails = _sample_emails()

        AIProcessor(_settings()).triage(emails)

        assert requests_mock.call_count == len(emails)

    def test_verdict_is_mapped_back_to_its_email(self, requests_mock):
        """Each reply must land on the email whose prompt produced it."""
        requests_mock.post(URL, [
            {"json": _reply("urgent", "first verdict")},
            {"json": _reply("tasks", "second verdict")},
            {"json": _reply("digest", "third verdict")},
        ])
        emails = _sample_emails()

        result = AIProcessor(_settings()).triage(emails)

        assert [(i.email_id, i.sender, i.subject, i.category, i.summary) for i in result.items] == [
            (emails[0]["id"], emails[0]["sender"], emails[0]["subject"], "urgent", "first verdict"),
            (emails[1]["id"], emails[1]["sender"], emails[1]["subject"], "tasks", "second verdict"),
            (emails[2]["id"], emails[2]["sender"], emails[2]["subject"], "digest", "third verdict"),
        ]

    def test_each_prompt_contains_only_its_own_email(self, requests_mock):
        requests_mock.post(URL, json=_reply("digest"))
        emails = _sample_emails()

        AIProcessor(_settings()).triage(emails)

        prompts = [r.json()["prompt"] for r in requests_mock.request_history]
        assert len(prompts) == len(emails)
        for email, prompt in zip(emails, prompts):
            assert email["subject"] in prompt
            for other in emails:
                if other is not email:
                    assert other["subject"] not in prompt

    def test_ignore_items_are_kept_in_the_result(self, requests_mock):
        """The processor reports everything; the notifier decides what to show."""
        requests_mock.post(URL, json=_reply("ignore", "spam"))

        result = AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert len(result.by_category("ignore")) == 1

    def test_progress_is_logged_per_email(self, requests_mock, caplog):
        requests_mock.post(URL, json=_reply("digest"))
        emails = _sample_emails()

        with caplog.at_level("INFO", logger="processor_ai"):
            AIProcessor(_settings()).triage(emails)

        assert f"Classifying 1/{len(emails)}" in caplog.text
        assert f"Classifying {len(emails)}/{len(emails)}" in caplog.text


# ---------------------------------------------------------------------------
# Tests: API request payload
# ---------------------------------------------------------------------------

class TestApiRequestPayload:
    def _last_body(self, requests_mock, **settings) -> dict:
        requests_mock.post(URL, json=_reply("digest"))
        AIProcessor(_settings(**settings)).triage(_sample_emails()[:1])
        return requests_mock.last_request.json()

    def test_correct_model_sent(self, requests_mock):
        assert self._last_body(requests_mock, ollama_model="mistral")["model"] == "mistral"

    def test_correct_url_called(self, requests_mock):
        self._last_body(requests_mock)

        assert requests_mock.last_request.url == URL

    def test_num_ctx_is_set_explicitly(self, requests_mock):
        """Ollama silently discards context past its default window, so it must be sent."""
        assert self._last_body(requests_mock)["options"]["num_ctx"] == 8192
        assert self._last_body(requests_mock, ollama_num_ctx=4096)["options"]["num_ctx"] == 4096

    def test_streaming_disabled(self, requests_mock):
        assert self._last_body(requests_mock)["stream"] is False

    def test_configured_timeout_is_used(self, requests_mock):
        self._last_body(requests_mock, ollama_timeout=7)

        assert requests_mock.last_request.timeout == 7

    def test_prompt_contains_email_content(self, requests_mock):
        prompt = self._last_body(requests_mock)["prompt"]

        assert "Interview invitation" in prompt
        assert "recruiter@techcorp.com" in prompt
        assert "confirm your availability" in prompt


# ---------------------------------------------------------------------------
# Tests: bounding the prompt fields
# ---------------------------------------------------------------------------

class TestFieldBounds:
    def _prompt_for(self, requests_mock, email: dict) -> str:
        requests_mock.post(URL, json=_reply("digest"))
        AIProcessor(_settings()).triage([email])
        return requests_mock.last_request.json()["prompt"]

    def test_subject_truncated_to_200_chars(self, requests_mock):
        email = {"id": "x", "subject": "S" * 500, "sender": "a@b.c", "body": "b"}

        prompt = self._prompt_for(requests_mock, email)

        assert "S" * 200 in prompt and "S" * 201 not in prompt

    def test_sender_truncated_to_100_chars(self, requests_mock):
        email = {"id": "x", "subject": "s", "sender": "N" * 300, "body": "b"}

        prompt = self._prompt_for(requests_mock, email)

        assert "N" * 100 in prompt and "N" * 101 not in prompt

    def test_result_keeps_the_full_untruncated_subject_and_sender(self, requests_mock):
        requests_mock.post(URL, json=_reply("digest"))
        email = {"id": "x", "subject": "S" * 500, "sender": "N" * 300, "body": "b"}

        item = AIProcessor(_settings()).triage([email]).items[0]

        assert item.subject == "S" * 500 and item.sender == "N" * 300

    def test_missing_fields_do_not_crash(self, requests_mock):
        requests_mock.post(URL, json=_reply("digest", "ok"))

        result = AIProcessor(_settings()).triage([{}])

        assert result.items[0].email_id == "" and result.items[0].summary == "ok"


# ---------------------------------------------------------------------------
# Tests: untrusted model output
# ---------------------------------------------------------------------------

class TestParsingUntrustedOutput:
    def _triage_one(self, requests_mock, text: str) -> TriageResult:
        requests_mock.post(URL, json=_raw_reply(text))
        return AIProcessor(_settings()).triage(_sample_emails()[:1])

    @pytest.mark.parametrize("category", ["URGENT", " Urgent ", "urgent\n"])
    def test_category_is_normalised(self, requests_mock, category):
        result = self._triage_one(requests_mock, json.dumps({"category": category, "summary": "s"}))

        assert result.items[0].category == "urgent"

    @pytest.mark.parametrize(
        "text",
        [
            "not json at all",
            "Sure! Here is the JSON: {\"category\": \"urgent\"}",
            "",
            "[]",
            '"urgent"',
            "null",
            '{"summary": "no category"}',
            '{"category": "critical", "summary": "invented category"}',
            '{"category": null, "summary": "s"}',
            '{"category": ["urgent"], "summary": "s"}',
        ],
    )
    def test_unusable_reply_is_recorded_as_failed_not_raised(self, requests_mock, text):
        result = self._triage_one(requests_mock, text)

        assert result.items == ()
        assert result.failed_ids == (_sample_emails()[0]["id"],)

    def test_summary_is_collapsed_to_a_single_line(self, requests_mock):
        result = self._triage_one(
            requests_mock, json.dumps({"category": "tasks", "summary": "line one\n\n  line   two\t!"})
        )

        assert result.items[0].summary == "line one line two !"

    @pytest.mark.parametrize("summary", [None, "", "   ", 12345, ["a", "b"], {"k": "v"}])
    def test_non_string_or_blank_summary_never_crashes(self, requests_mock, summary):
        result = self._triage_one(requests_mock, json.dumps({"category": "digest", "summary": summary}))

        assert isinstance(result.items[0].summary, str)

    def test_blank_summary_falls_back_to_subject(self, requests_mock):
        result = self._triage_one(requests_mock, json.dumps({"category": "digest", "summary": ""}))

        assert result.items[0].summary == _sample_emails()[0]["subject"]

    def test_extra_keys_are_ignored(self, requests_mock):
        result = self._triage_one(
            requests_mock, json.dumps({"category": "digest", "summary": "s", "confidence": 0.9})
        )

        assert result.items[0].summary == "s"


# ---------------------------------------------------------------------------
# Tests: failure isolation
# ---------------------------------------------------------------------------

class TestFailureIsolation:
    def test_one_bad_reply_does_not_lose_the_others(self, requests_mock):
        emails = [
            {"id": f"m{i}", "subject": f"S{i}", "sender": "a@b.c", "body": "b"} for i in range(10)
        ]
        replies = [{"json": _reply("digest", f"ok {i}")} for i in range(10)]
        replies[4] = {"json": _raw_reply("garbage")}
        requests_mock.post(URL, replies)

        result = AIProcessor(_settings()).triage(emails)

        assert len(result.items) == 9
        assert result.failed_ids == ("m4",)
        assert "m4" not in [i.email_id for i in result.items]

    def test_failure_is_logged(self, requests_mock, caplog):
        requests_mock.post(URL, json=_raw_reply("garbage"))

        with caplog.at_level("WARNING", logger="processor_ai"):
            AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert "Could not classify" in caplog.text

    def test_http_error_propagates_and_aborts_the_run(self, requests_mock):
        """Ollama being broken is not a per-email problem; fail loudly."""
        requests_mock.post(URL, status_code=503)

        with pytest.raises(requests.HTTPError):
            AIProcessor(_settings()).triage(_sample_emails())

        assert requests_mock.call_count == 1

    def test_connection_error_propagates(self, requests_mock):
        requests_mock.post(URL, exc=requests.exceptions.ConnectionError)

        with pytest.raises(requests.exceptions.ConnectionError):
            AIProcessor(_settings()).triage(_sample_emails())

    def test_classification_error_is_a_plain_exception(self):
        assert issubclass(ClassificationError, Exception)


# ---------------------------------------------------------------------------
# Tests: verbose (DEBUG) logging of prompt and raw reply
# ---------------------------------------------------------------------------

class TestDebugLogging:
    def test_prompt_and_raw_reply_logged_at_debug(self, requests_mock, caplog):
        requests_mock.post(URL, json=_reply("urgent", "raw-reply-marker"))

        with caplog.at_level("DEBUG", logger="processor_ai"):
            AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert "Ollama prompt" in caplog.text
        assert "Interview invitation" in caplog.text
        assert "raw-reply-marker" in caplog.text

    def test_nothing_logged_at_info(self, requests_mock, caplog):
        requests_mock.post(URL, json=_reply("digest"))

        with caplog.at_level("INFO", logger="processor_ai"):
            AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert "Ollama prompt" not in caplog.text
