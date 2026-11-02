"""Tests for the AI processor module.

All Ollama API calls are mocked via requests-mock so no running Ollama
instance is required.
"""

from __future__ import annotations

import json

import pytest
import requests

from config import Settings
from processor_ai import (
    CATEGORIES,
    AIProcessor,
    ClassificationError,
    OllamaRequestError,
    TriagedItem,
    TriageResult,
)


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
VERSION_URL = "http://localhost:11434/api/version"


@pytest.fixture(autouse=True)
def ollama_version(requests_mock):
    """Every processor probes the server version once; assume one that takes a schema."""
    requests_mock.get(VERSION_URL, json={"version": "0.5.0"})


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    """Record retry backoff delays instead of waiting them out."""
    delays: list[float] = []
    monkeypatch.setattr("processor_ai.time.sleep", delays.append)
    return delays


def _generate_calls(requests_mock) -> list:
    """Just the classification calls, without the version probe."""
    return [r for r in requests_mock.request_history if r.url == URL]


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

        assert len(_generate_calls(requests_mock)) == len(emails)

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

        prompts = [r.json()["prompt"] for r in _generate_calls(requests_mock)]
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

    def test_temperature_is_zero(self, requests_mock):
        """The same inbox twice should give the same briefing."""
        assert self._last_body(requests_mock)["options"]["temperature"] == 0

    def test_format_carries_the_json_schema(self, requests_mock):
        schema = self._last_body(requests_mock)["format"]

        assert schema["type"] == "object"
        assert schema["required"] == ["category", "summary"]
        assert schema["properties"]["category"]["enum"] == list(CATEGORIES)
        assert schema["properties"]["summary"]["type"] == "string"

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
# Tests: choosing the response format from the server version
# ---------------------------------------------------------------------------

class TestResponseFormat:
    def _format_sent(self, requests_mock) -> object:
        requests_mock.post(URL, json=_reply("digest"))
        AIProcessor(_settings()).triage(_sample_emails()[:1])
        return requests_mock.last_request.json()["format"]

    @pytest.mark.parametrize(
        "version",
        ["0.5.0", "0.5.1", "0.5.0-rc1", "0.40.13", "1.0.0"],
    )
    def test_supported_versions_get_the_schema(self, requests_mock, version):
        """0.40.13 is the trap: as text it sorts below 0.5.0."""
        requests_mock.get(VERSION_URL, json={"version": version})

        assert isinstance(self._format_sent(requests_mock), dict)

    @pytest.mark.parametrize("version", ["0.4.7", "0.1.0", "0.0.0", "unknown", ""])
    def test_older_or_unreadable_versions_get_plain_json(self, requests_mock, version):
        requests_mock.get(VERSION_URL, json={"version": version})

        assert self._format_sent(requests_mock) == "json"

    @pytest.mark.parametrize(
        "response",
        [
            {"status_code": 404},
            {"status_code": 500},
            {"text": "not json"},
            {"json": {}},
            {"json": {"version": 5}},
            {"exc": requests.exceptions.ConnectTimeout},
        ],
    )
    def test_failed_version_probe_falls_back_to_plain_json(self, requests_mock, response):
        """Never risk a schema on a server that may reject it for every email."""
        requests_mock.get(VERSION_URL, **response)

        assert self._format_sent(requests_mock) == "json"

    def test_fallback_is_logged(self, requests_mock, caplog):
        requests_mock.get(VERSION_URL, json={"version": "0.4.7"})

        with caplog.at_level("WARNING", logger="processor_ai"):
            self._format_sent(requests_mock)

        assert "plain JSON mode" in caplog.text

    def test_version_is_probed_once_per_processor(self, requests_mock):
        requests_mock.post(URL, json=_reply("digest"))

        AIProcessor(_settings()).triage(_sample_emails())

        probes = [r for r in requests_mock.request_history if r.url == VERSION_URL]
        assert len(probes) == 1


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
            '{"category": "urgent"}',
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
# Tests: guarding the shape of Ollama's response body
# ---------------------------------------------------------------------------

class TestResponseShapeGuard:
    @pytest.mark.parametrize(
        "response",
        [
            {"json": {"error": "something went wrong"}},
            {"json": {"done": True}},
            {"json": []},
            {"json": "just a string"},
            {"json": {"response": None}},
            {"json": {"response": 5}},
            {"text": "<html>not json</html>"},
        ],
    )
    def test_malformed_response_is_retried_then_recorded_not_raised(self, requests_mock, response):
        requests_mock.post(URL, **response)

        result = AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert result.failed_ids == ("msg1",)
        assert len(_generate_calls(requests_mock)) == 3


# ---------------------------------------------------------------------------
# Tests: retrying what is retryable
# ---------------------------------------------------------------------------

_GOOD = {"json": _reply("urgent", "recovered")}


class TestRetry:
    @pytest.mark.parametrize(
        "failure",
        [
            {"status_code": 500},
            {"status_code": 503},
            {"exc": requests.exceptions.ConnectionError},
            {"exc": requests.exceptions.ConnectTimeout},
            {"exc": requests.exceptions.ReadTimeout},
            {"exc": requests.exceptions.ChunkedEncodingError},
            {"json": _raw_reply("Sure! Here you go: not json")},
            {"json": _raw_reply('{"category": "critical", "summary": "invented"}')},
        ],
    )
    def test_retry_then_success(self, requests_mock, sleeps, failure):
        requests_mock.post(URL, [failure, _GOOD])

        result = AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert [i.summary for i in result.items] == ["recovered"]
        assert result.failed_ids == ()
        assert sleeps == [1]

    def test_succeeds_on_the_last_attempt(self, requests_mock, sleeps):
        requests_mock.post(URL, [{"status_code": 503}, {"status_code": 503}, _GOOD])

        result = AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert len(result.items) == 1
        assert sleeps == [1, 2]

    def test_exhaustion_raises_classification_error(self, requests_mock, sleeps):
        requests_mock.post(URL, status_code=503)

        with pytest.raises(ClassificationError):
            AIProcessor(_settings())._classify_one(_sample_emails()[0])

        assert len(_generate_calls(requests_mock)) == 3
        assert sleeps == [1, 2]

    @pytest.mark.parametrize(
        ("retries", "calls", "delays"),
        [(0, 1, []), (1, 2, [1]), (2, 3, [1, 2]), (3, 4, [1, 2, 4])],
    )
    def test_ollama_retries_setting_is_honoured(self, requests_mock, sleeps, retries, calls, delays):
        requests_mock.post(URL, json=_raw_reply("garbage"))

        AIProcessor(_settings(ollama_retries=retries)).triage(_sample_emails()[:1])

        assert len(_generate_calls(requests_mock)) == calls
        assert sleeps == delays

    @pytest.mark.parametrize("status", [400, 401, 404, 422, 429])
    def test_4xx_is_never_retried(self, requests_mock, sleeps, status):
        requests_mock.post(URL, status_code=status)

        with pytest.raises(ClassificationError):
            AIProcessor(_settings())._classify_one(_sample_emails()[0])

        assert len(_generate_calls(requests_mock)) == 1
        assert sleeps == []

    def test_unusable_reply_is_retried_with_some_randomness(self, requests_mock):
        """At temperature 0 the model replays the same bad reply word for word."""
        requests_mock.post(URL, [{"json": _raw_reply("garbage")}, {"json": _raw_reply("garbage")}, _GOOD])

        AIProcessor(_settings()).triage(_sample_emails()[:1])

        temperatures = [r.json()["options"]["temperature"] for r in _generate_calls(requests_mock)]
        assert temperatures[0] == 0
        assert temperatures[1] > 0 and temperatures[2] > 0

    def test_transient_failure_is_retried_at_temperature_zero(self, requests_mock):
        """The reply was never judged unusable, so determinism need not be given up."""
        requests_mock.post(URL, [{"status_code": 503}, {"exc": requests.exceptions.ReadTimeout}, _GOOD])

        AIProcessor(_settings()).triage(_sample_emails()[:1])

        temperatures = [r.json()["options"]["temperature"] for r in _generate_calls(requests_mock)]
        assert temperatures == [0, 0, 0]

    def test_retry_is_logged_without_the_reply_itself(self, requests_mock, caplog):
        """A reply echoes the email; only --verbose may put that in the log."""
        requests_mock.post(URL, json=_raw_reply("Private detail from the email"))

        with caplog.at_level("INFO", logger="processor_ai"):
            AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert "retrying in 1s" in caplog.text
        assert "Could not classify" in caplog.text
        assert "Private detail" not in caplog.text


# ---------------------------------------------------------------------------
# Tests: failure isolation
# ---------------------------------------------------------------------------

def _ten_emails() -> list[dict]:
    return [{"id": f"m{i}", "subject": f"S{i}", "sender": "a@b.c", "body": "b"} for i in range(10)]


def _subject_of(request) -> str:
    return request.json()["prompt"].split("Subject: ", 1)[1].split("\n", 1)[0]


class TestFailureIsolation:
    def test_one_bad_reply_does_not_lose_the_others(self, requests_mock):
        def reply(request, context):
            subject = _subject_of(request)
            return _raw_reply("garbage") if subject == "S4" else _reply("digest", f"ok {subject}")

        requests_mock.post(URL, json=reply)

        result = AIProcessor(_settings()).triage(_ten_emails())

        assert len(result.items) == 9
        assert result.failed_ids == ("m4",)
        assert "m4" not in [i.email_id for i in result.items]

    def test_one_failed_request_does_not_abort_a_batch_of_ten(self, requests_mock):
        def reply(request, context):
            if _subject_of(request) == "S4":
                context.status_code = 503
                return {}
            return _reply("digest")

        requests_mock.post(URL, json=reply)

        result = AIProcessor(_settings()).triage(_ten_emails())

        assert len(result.items) == 9
        assert result.failed_ids == ("m4",)

    def test_failure_is_logged(self, requests_mock, caplog):
        requests_mock.post(URL, json=_raw_reply("garbage"))

        with caplog.at_level("WARNING", logger="processor_ai"):
            AIProcessor(_settings()).triage(_sample_emails()[:1])

        assert "Could not classify" in caplog.text

    def test_classification_error_is_a_plain_exception(self):
        assert issubclass(ClassificationError, Exception)


class TestOllamaKeepsFailing:
    """A dead Ollama aborts the run rather than failing every email, slowly."""

    @pytest.mark.parametrize(
        "failure",
        [{"status_code": 503}, {"exc": requests.exceptions.ConnectionError}, {"exc": requests.exceptions.ReadTimeout}],
    )
    def test_three_emails_in_a_row_abort_the_run(self, requests_mock, failure):
        requests_mock.post(URL, **failure)

        with pytest.raises(OllamaRequestError, match="3 emails in a row"):
            AIProcessor(_settings()).triage(_ten_emails())

        assert len(_generate_calls(requests_mock)) == 9  # 3 emails, 3 attempts each

    def test_repeated_4xx_also_aborts_but_quickly(self, requests_mock):
        requests_mock.post(URL, status_code=404)

        with pytest.raises(OllamaRequestError, match="404"):
            AIProcessor(_settings()).triage(_ten_emails())

        assert len(_generate_calls(requests_mock)) == 3

    def test_two_in_a_row_then_a_success_carries_on(self, requests_mock):
        def reply(request, context):
            if _subject_of(request) in ("S0", "S1"):
                context.status_code = 503
                return {}
            return _reply("digest")

        requests_mock.post(URL, json=reply)

        result = AIProcessor(_settings()).triage(_ten_emails())

        assert len(result.items) == 8
        assert result.failed_ids == ("m0", "m1")

    def test_a_success_resets_the_streak(self, requests_mock):
        def reply(request, context):
            if _subject_of(request) in ("S0", "S1", "S3", "S4", "S6", "S7"):
                context.status_code = 503
                return {}
            return _reply("digest")

        requests_mock.post(URL, json=reply)

        result = AIProcessor(_settings()).triage(_ten_emails())

        assert result.failed_ids == ("m0", "m1", "m3", "m4", "m6", "m7")
        assert len(result.items) == 4

    def test_an_unusable_reply_resets_the_streak(self, requests_mock):
        """A garbled reply still proves Ollama is answering."""
        def reply(request, context):
            subject = _subject_of(request)
            if subject in ("S0", "S1", "S3", "S4"):
                context.status_code = 503
                return {}
            return _raw_reply("garbage") if subject == "S2" else _reply("digest")

        requests_mock.post(URL, json=reply)

        result = AIProcessor(_settings()).triage(_ten_emails())

        assert result.failed_ids == ("m0", "m1", "m2", "m3", "m4")
        assert len(result.items) == 5

    def test_unusable_replies_alone_never_abort(self, requests_mock):
        requests_mock.post(URL, json=_raw_reply("garbage"))

        result = AIProcessor(_settings()).triage(_ten_emails())

        assert len(result.failed_ids) == 10


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
