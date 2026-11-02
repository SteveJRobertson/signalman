"""Tests for the main orchestrator module.

All external dependencies (GmailProvider, AIProcessor, SignalNotifier, load_dotenv)
are mocked so the test suite requires no real credentials or running services.
"""

from __future__ import annotations

import logging
import runpy
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests
from google.auth.exceptions import RefreshError

from config import Settings
from notifier_signal import SignalNotifier
from processor_ai import AIProcessor, ModelNotFoundError, TriagedItem, TriageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def no_real_http(requests_mock):
    """Failure handling now sends a real alert, and an earlier ``import main`` may
    have loaded a real .env. Anything not mocked must fail here, never reach Signal."""


def _settings(**overrides) -> Settings:
    """Build a Settings instance with sane test defaults."""
    base = {
        "signal_sender": "+10000000001",
        "signal_recipient": "+10000000002",
    }
    base.update(overrides)
    return Settings(**base)


def _sample_emails() -> list[dict]:
    return [
        {
            "id": "msg1",
            "subject": "Interview invite – please respond today",
            "sender": "recruiter@techcorp.com",
            "body": "Confirm your availability by end of day.",
        }
    ]


def _item(category: str, summary: str) -> TriagedItem:
    return TriagedItem(
        email_id="msg1", sender="a@b.c", subject="subj", category=category, summary=summary
    )


def _sample_triage() -> TriageResult:
    return TriageResult(
        items=(
            _item("urgent", "Interview invite from techcorp.com – confirm today"),
            _item("tasks", "Return school trip permission slip by Friday"),
            _item("digest", "Weekly newsletter summary"),
        )
    )


# ---------------------------------------------------------------------------
# Tests: successful run
# ---------------------------------------------------------------------------

class TestRunSuccess:
    """run(settings) wires GmailProvider → AIProcessor → SignalNotifier correctly."""

    def _run_with_mocks(self, emails=None, triage=None, settings=None):
        """Execute main.run(settings) with all external dependencies mocked."""
        if emails is None:
            emails = _sample_emails()
        if triage is None:
            triage = _sample_triage()
        if settings is None:
            settings = _settings()

        mock_provider = MagicMock()
        mock_provider.fetch_unread_emails.return_value = emails

        mock_processor = MagicMock()
        mock_processor.triage.return_value = triage

        mock_notifier = MagicMock()

        with (
            patch("main.GmailProvider") as mock_gmail_cls,
            patch("main.AIProcessor") as mock_ai_cls,
            patch("main.SignalNotifier") as mock_signal_cls,
        ):
            mock_gmail_cls.from_credentials.return_value = mock_provider
            mock_ai_cls.return_value = mock_processor
            mock_signal_cls.return_value = mock_notifier

            import main
            main.run(settings)

        return {
            "gmail_cls": mock_gmail_cls,
            "ai_cls": mock_ai_cls,
            "signal_cls": mock_signal_cls,
            "provider": mock_provider,
            "processor": mock_processor,
            "notifier": mock_notifier,
            "settings": settings,
        }

    def test_gmail_provider_from_credentials_called_with_settings(self):
        mocks = self._run_with_mocks()
        mocks["gmail_cls"].from_credentials.assert_called_once_with(mocks["settings"])

    def test_fetch_unread_emails_called(self):
        mocks = self._run_with_mocks()
        mocks["provider"].fetch_unread_emails.assert_called_once()

    def test_ai_processor_constructed_with_settings(self):
        mocks = self._run_with_mocks()
        mocks["ai_cls"].assert_called_once_with(mocks["settings"])

    def test_ai_processor_receives_emails(self):
        emails = _sample_emails()
        mocks = self._run_with_mocks(emails=emails)
        mocks["processor"].triage.assert_called_once_with(emails)

    def test_signal_notifier_constructed_with_settings(self):
        mocks = self._run_with_mocks()
        mocks["signal_cls"].assert_called_once_with(mocks["settings"])

    def test_signal_notifier_receives_triage(self):
        triage = _sample_triage()
        mocks = self._run_with_mocks(triage=triage)
        mocks["notifier"].send.assert_called_once_with(triage)

    def test_empty_inbox_completes_without_error(self):
        """An empty inbox should still send an 'all clear' briefing."""
        triage = TriageResult()
        mocks = self._run_with_mocks(emails=[], triage=triage)
        mocks["notifier"].send.assert_called_once_with(triage)


# ---------------------------------------------------------------------------
# Tests: triage summary logging
# ---------------------------------------------------------------------------

class TestTriageLogging:
    def test_category_counts_logged(self, caplog):
        import main

        result = TriageResult(
            items=(_item("urgent", "a"), _item("urgent", "b"), _item("ignore", "c")),
            failed_ids=("x",),
        )
        processor = MagicMock()
        processor.triage.return_value = result
        with (
            patch("main.GmailProvider"),
            patch("main.AIProcessor", return_value=processor),
            patch("main.SignalNotifier"),
            caplog.at_level("INFO", logger="main"),
        ):
            main.run(_settings())

        assert "urgent=2, tasks=0, digest=0, ignored=1, failed=1" in caplog.text


# ---------------------------------------------------------------------------
# Tests: body cleaning between fetch and triage
# ---------------------------------------------------------------------------

class TestBodyCleaning:
    def _triage_input(self, emails, settings=None):
        """Run main.run with mocks and return what the processor was given."""
        import main

        processor = MagicMock()
        processor.triage.return_value = TriageResult()
        with (
            patch("main.GmailProvider") as gmail_cls,
            patch("main.AIProcessor", return_value=processor),
            patch("main.SignalNotifier"),
        ):
            gmail_cls.from_credentials.return_value.fetch_unread_emails.return_value = emails
            main.run(settings or _settings())
        return processor.triage.call_args.args[0]

    def test_quoted_history_and_signature_removed_before_triage(self):
        body = "Yes, 3pm works.\n\nOn Mon, X wrote:\n> old\n\n-- \nSig"
        emails = [{"id": "a", "subject": "S", "sender": "x@y.z", "body": body}]

        result = self._triage_input(emails)

        assert result[0]["body"] == "Yes, 3pm works."

    def test_body_is_bounded_by_max_body_chars(self):
        emails = [{"id": "a", "subject": "S", "sender": "x@y.z", "body": "word " * 1000}]

        result = self._triage_input(emails, _settings(max_body_chars=200))

        assert len(result[0]["body"]) <= 200

    def test_other_fields_untouched_and_input_not_mutated(self):
        emails = [{"id": "a", "subject": "Subj", "sender": "x@y.z", "body": "hi\n\n\n\nthere"}]

        result = self._triage_input(emails)

        assert (result[0]["id"], result[0]["subject"], result[0]["sender"]) == ("a", "Subj", "x@y.z")
        assert emails[0]["body"] == "hi\n\n\n\nthere"

    def test_empty_body_stays_empty_string(self):
        emails = [{"id": "a", "subject": "S", "sender": "x@y.z", "body": ""}]

        assert self._triage_input(emails)[0]["body"] == ""


# ---------------------------------------------------------------------------
# Tests: readiness gate
# ---------------------------------------------------------------------------

OLLAMA = "http://ollama.test"
SIGNAL = "http://signal.test"
_MODELS = {"models": [{"name": "llama3:latest"}]}


class TestReadinessGate:
    @pytest.fixture(autouse=True)
    def sleeps(self, monkeypatch) -> list[float]:
        delays: list[float] = []
        monkeypatch.setattr("main.time.sleep", delays.append)
        return delays

    def _wait(self, **overrides) -> None:
        import main

        settings = _settings(ollama_url=OLLAMA, signal_api_url=SIGNAL, **overrides)
        main.wait_until_ready(settings, AIProcessor(settings), SignalNotifier(settings))

    def test_ready_at_once_never_sleeps(self, requests_mock, sleeps):
        requests_mock.get(f"{OLLAMA}/api/tags", json=_MODELS)
        requests_mock.get(f"{SIGNAL}/v1/about", json={})

        self._wait()

        assert sleeps == []

    def test_retries_then_succeeds(self, requests_mock, sleeps):
        """The 08:00-after-a-restart case: both come up within the minute."""
        down = {"exc": requests.exceptions.ConnectionError}
        requests_mock.get(f"{OLLAMA}/api/tags", [down, down, {"json": _MODELS}])
        requests_mock.get(f"{SIGNAL}/v1/about", [down, {"status_code": 503}, {"json": {}}])

        self._wait()

        assert sleeps == [10, 10]

    def test_succeeds_on_the_very_last_attempt(self, requests_mock, sleeps):
        down = {"exc": requests.exceptions.ConnectionError}
        requests_mock.get(f"{OLLAMA}/api/tags", [down] * 5 + [{"json": _MODELS}])
        requests_mock.get(f"{SIGNAL}/v1/about", json={})

        self._wait()

        assert sleeps == [10] * 5

    def test_exhausts_and_raises_naming_both_services(self, requests_mock, sleeps):
        requests_mock.get(f"{OLLAMA}/api/tags", exc=requests.exceptions.ConnectionError)
        requests_mock.get(f"{SIGNAL}/v1/about", exc=requests.exceptions.ConnectionError)

        with pytest.raises(ConnectionError, match="after 6 attempts") as exc_info:
            self._wait()

        assert "Ollama is unreachable" in str(exc_info.value)
        assert "Signal API container is unreachable" in str(exc_info.value)
        assert sleeps == [10] * 5  # none after the last attempt
        assert sum(r.path == "/api/tags" for r in requests_mock.request_history) == 6

    def test_error_names_only_what_is_still_down(self, requests_mock):
        requests_mock.get(f"{OLLAMA}/api/tags", json=_MODELS)
        requests_mock.get(f"{SIGNAL}/v1/about", exc=requests.exceptions.ConnectionError)

        with pytest.raises(ConnectionError) as exc_info:
            self._wait()

        assert "Signal" in str(exc_info.value)
        assert "Ollama" not in str(exc_info.value)

    def test_attempts_and_interval_come_from_settings(self, requests_mock, sleeps):
        requests_mock.get(f"{OLLAMA}/api/tags", exc=requests.exceptions.ConnectionError)
        requests_mock.get(f"{SIGNAL}/v1/about", json={})

        with pytest.raises(ConnectionError, match="after 3 attempts"):
            self._wait(readiness_attempts=3, readiness_interval=30)

        assert sleeps == [30, 30]

    def test_missing_model_raises_at_once(self, requests_mock, sleeps):
        """Waiting will not install it, so name the real problem straight away."""
        requests_mock.get(f"{OLLAMA}/api/tags", json={"models": [{"name": "mistral:latest"}]})
        requests_mock.get(f"{SIGNAL}/v1/about", json={})

        with pytest.raises(ModelNotFoundError, match="llama3"):
            self._wait()

        assert sleeps == []

    def test_dry_run_neither_needs_nor_contacts_signal(self, requests_mock, sleeps):
        requests_mock.get(f"{OLLAMA}/api/tags", json=_MODELS)

        self._wait(dry_run=True)

        assert {r.hostname for r in requests_mock.request_history} == {"ollama.test"}
        assert sleeps == []

    def test_waiting_is_logged(self, requests_mock, caplog):
        down = {"exc": requests.exceptions.ConnectionError}
        requests_mock.get(f"{OLLAMA}/api/tags", [down, {"json": _MODELS}])
        requests_mock.get(f"{SIGNAL}/v1/about", json={})

        with caplog.at_level("INFO", logger="main"):
            self._wait()

        assert "Not ready (attempt 1/6)" in caplog.text
        assert "Retrying in 10s" in caplog.text

    def test_run_stops_before_gmail_when_not_ready(self):
        import main

        with (
            patch("main.GmailProvider") as gmail_cls,
            patch("main.AIProcessor") as ai_cls,
            patch("main.SignalNotifier"),
            patch("main.time.sleep"),
        ):
            ai_cls.return_value.check_ready.side_effect = ConnectionError("Ollama is down.")
            with pytest.raises(ConnectionError, match="Ollama is down"):
                main.run(_settings())

        gmail_cls.from_credentials.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: mail Gmail would not hand over still shows in the briefing
# ---------------------------------------------------------------------------

class TestFetchFailuresReachTheBriefing:
    def test_failed_ids_are_handed_to_the_notifier(self):
        import main

        triage = TriageResult(items=(_item("urgent", "a"),))
        with (
            patch("main.GmailProvider") as gmail_cls,
            patch("main.AIProcessor") as ai_cls,
            patch("main.SignalNotifier") as signal_cls,
        ):
            provider = gmail_cls.from_credentials.return_value
            provider.fetch_unread_emails.return_value = []
            provider.failed_ids = ["bad1", "bad2"]
            ai_cls.return_value.triage.return_value = triage
            main.run(_settings())

        sent = signal_cls.return_value.send.call_args.args[0]
        assert sent.fetch_failed_ids == ("bad1", "bad2")
        assert sent.items == triage.items


# ---------------------------------------------------------------------------
# Tests: __main__ block error handling
# ---------------------------------------------------------------------------
#
# Environment-driven failures (missing required vars, bad numeric/URL values)
# are exercised against config.Settings.from_env() directly in
# tests/test_config.py. These tests instead run the real __main__ entrypoint
# via runpy rather than mocking main.run(), covering what happens to a failure
# from there on: a Signal alert and a clean SystemExit(1).

def _arrange_main_block(monkeypatch, *argv: str, **env: str) -> None:
    """Prepare to run main.py's real __main__ block against the mocked services.

    Every setting that matters is pinned, because an earlier ``import main`` may
    already have loaded a real .env into os.environ.
    """
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "argv", ["main.py", *argv])
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    values = {
        "SIGNAL_SENDER_NUMBER": "+10000000001",
        "SIGNAL_RECIPIENT_NUMBER": "+10000000002",
        "SIGNAL_API_URL": SIGNAL,
        "OLLAMA_URL": OLLAMA,
        "OLLAMA_MODEL": "llama3",
        "READINESS_ATTEMPTS": "2",
        "READINESS_INTERVAL": "0",
        **env,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _services_up(requests_mock) -> None:
    requests_mock.get(f"{OLLAMA}/api/tags", json=_MODELS)
    requests_mock.get(f"{SIGNAL}/v1/about", json={})
    requests_mock.post(f"{SIGNAL}/v2/send", status_code=201)


def _gmail_fails_with(monkeypatch, error: Exception) -> None:
    import provider_gmail

    monkeypatch.setattr(
        provider_gmail.GmailProvider, "from_credentials", MagicMock(side_effect=error)
    )


def _exit_code_of_main_block() -> int:
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("main", run_name="__main__", alter_sys=True)
    return exc_info.value.code


def _messages_sent(requests_mock) -> list[str]:
    return [
        r.json()["message"]
        for r in requests_mock.request_history
        if r.method == "POST" and r.path == "/v2/send"
    ]


class TestMainBlockErrorHandling:
    def test_key_error_causes_sys_exit_1(self, monkeypatch, requests_mock, caplog):
        """A missing env var causes SystemExit(1) via the real __main__ handler."""
        _arrange_main_block(monkeypatch)
        monkeypatch.delenv("SIGNAL_SENDER_NUMBER", raising=False)
        monkeypatch.delenv("SIGNAL_RECIPIENT_NUMBER", raising=False)

        assert _exit_code_of_main_block() == 1
        assert "Missing required environment variable" in caplog.text
        assert requests_mock.call_count == 0  # nobody to alert yet

    def test_invalid_setting_causes_sys_exit_1(self, monkeypatch, requests_mock, caplog):
        _arrange_main_block(monkeypatch, READINESS_ATTEMPTS="many")

        assert _exit_code_of_main_block() == 1
        assert "READINESS_ATTEMPTS" in caplog.text
        assert requests_mock.call_count == 0

    def test_generic_exception_causes_sys_exit_1(self, monkeypatch, requests_mock):
        """Any unexpected exception causes SystemExit(1) via the real __main__ handler."""
        _arrange_main_block(monkeypatch)
        _services_up(requests_mock)
        _gmail_fails_with(monkeypatch, RuntimeError("boom"))

        assert _exit_code_of_main_block() == 1

    def test_key_error_mid_run_is_not_mistaken_for_a_missing_variable(
        self, monkeypatch, requests_mock, caplog
    ):
        _arrange_main_block(monkeypatch)
        _services_up(requests_mock)
        _gmail_fails_with(monkeypatch, KeyError("payload"))

        assert _exit_code_of_main_block() == 1
        assert "Missing required environment variable" not in caplog.text
        assert "KeyError: 'payload'" in _messages_sent(requests_mock)[0]

    def test_a_good_run_exits_cleanly_and_sends_the_briefing(self, monkeypatch, requests_mock):
        import provider_gmail

        _arrange_main_block(monkeypatch)
        _services_up(requests_mock)
        provider = MagicMock(failed_ids=[])
        provider.fetch_unread_emails.return_value = []
        monkeypatch.setattr(
            provider_gmail.GmailProvider, "from_credentials", MagicMock(return_value=provider)
        )

        runpy.run_module("main", run_name="__main__", alter_sys=True)  # no SystemExit

        messages = _messages_sent(requests_mock)
        assert len(messages) == 1
        assert "All clear" in messages[0]  # the heartbeat


class TestFailureAlert:
    def test_failure_sends_an_alert_and_exits_1(self, monkeypatch, requests_mock):
        _arrange_main_block(monkeypatch)
        _services_up(requests_mock)
        _gmail_fails_with(monkeypatch, RuntimeError("boom"))

        assert _exit_code_of_main_block() == 1

        messages = _messages_sent(requests_mock)
        assert len(messages) == 1
        lines = messages[0].splitlines()
        assert lines[0] == "🔴 Signalman failed"
        assert lines[1] == "RuntimeError: boom"
        assert lines[2].startswith("20")

    @pytest.mark.parametrize(
        "failure",
        [{"status_code": 500}, {"status_code": 403}, {"exc": requests.exceptions.ConnectionError}],
    )
    def test_alert_failure_is_logged_and_exits_1_without_raising(
        self, monkeypatch, requests_mock, caplog, failure
    ):
        _arrange_main_block(monkeypatch)
        _services_up(requests_mock)
        requests_mock.post(f"{SIGNAL}/v2/send", **failure)
        _gmail_fails_with(monkeypatch, RuntimeError("boom"))

        assert _exit_code_of_main_block() == 1
        assert "Could not send the failure alert" in caplog.text
        assert "Signalman failed: RuntimeError: boom" in caplog.text

    def test_dry_run_failure_prints_the_alert_and_sends_nothing(
        self, monkeypatch, requests_mock, capsys
    ):
        _arrange_main_block(monkeypatch, "--dry-run")
        _services_up(requests_mock)
        _gmail_fails_with(monkeypatch, RuntimeError("boom"))

        assert _exit_code_of_main_block() == 1
        assert "RuntimeError: boom" in capsys.readouterr().out
        assert _messages_sent(requests_mock) == []

    def test_ollama_down_produces_an_alert(self, monkeypatch, requests_mock):
        """The plan's definition of done: stopping Ollama reaches your phone."""
        _arrange_main_block(monkeypatch)
        _services_up(requests_mock)
        requests_mock.get(f"{OLLAMA}/api/tags", exc=requests.exceptions.ConnectionError)

        assert _exit_code_of_main_block() == 1

        message = _messages_sent(requests_mock)[0]
        assert "ConnectionError" in message and "Ollama is unreachable" in message

    def test_signal_down_is_a_clear_error_not_a_stack_trace(
        self, monkeypatch, requests_mock, caplog
    ):
        _arrange_main_block(monkeypatch)
        _services_up(requests_mock)
        requests_mock.get(f"{SIGNAL}/v1/about", exc=requests.exceptions.ConnectionError)
        requests_mock.post(f"{SIGNAL}/v2/send", exc=requests.exceptions.ConnectionError)

        assert _exit_code_of_main_block() == 1
        assert "Signal API container is unreachable" in caplog.text
        assert "Could not send the failure alert" in caplog.text
        assert "Traceback" not in caplog.text

    def test_missing_model_produces_an_alert_naming_it(self, monkeypatch, requests_mock):
        _arrange_main_block(monkeypatch)
        _services_up(requests_mock)
        requests_mock.get(f"{OLLAMA}/api/tags", json={"models": []})

        assert _exit_code_of_main_block() == 1

        message = _messages_sent(requests_mock)[0]
        assert "ModelNotFoundError" in message and "ollama pull llama3" in message

    def test_ollama_dying_mid_run_produces_an_alert(self, monkeypatch, requests_mock):
        import provider_gmail

        _arrange_main_block(monkeypatch)
        _services_up(requests_mock)
        requests_mock.get(f"{OLLAMA}/api/version", json={"version": "0.5.0"})
        requests_mock.post(f"{OLLAMA}/api/generate", exc=requests.exceptions.ConnectionError)
        provider = MagicMock(failed_ids=[])
        provider.fetch_unread_emails.return_value = [
            {"id": str(i), "subject": "s", "sender": "a@b.c", "body": "b"} for i in range(5)
        ]
        monkeypatch.setattr(
            provider_gmail.GmailProvider, "from_credentials", MagicMock(return_value=provider)
        )

        assert _exit_code_of_main_block() == 1

        messages = _messages_sent(requests_mock)
        assert len(messages) == 1  # the alert, and no briefing
        assert "OllamaRequestError" in messages[0] and "3 emails in a row" in messages[0]


class TestGmailFailuresWhenUnattended:
    """Real from_credentials; pytest's stdin, like launchd's, is not a terminal."""

    def test_missing_token_alerts_instead_of_waiting_forever(
        self, monkeypatch, requests_mock, tmp_path
    ):
        _arrange_main_block(monkeypatch, GMAIL_TOKEN_PATH=str(tmp_path / "token.json"))
        _services_up(requests_mock)

        with patch("provider_gmail.InstalledAppFlow") as flow_cls:
            assert _exit_code_of_main_block() == 1

        flow_cls.from_client_secrets_file.assert_not_called()
        message = _messages_sent(requests_mock)[0]
        assert "GmailAuthError" in message
        assert "python3 main.py --dry-run --limit 1" in message

    def test_revoked_refresh_token_alerts(self, monkeypatch, requests_mock, tmp_path):
        token = tmp_path / "token.json"
        token.write_text("{}")
        _arrange_main_block(monkeypatch, GMAIL_TOKEN_PATH=str(token))
        _services_up(requests_mock)
        creds = MagicMock(valid=False, expired=True, refresh_token="r")
        creds.refresh.side_effect = RefreshError("invalid_grant: Token has been expired or revoked.")

        with (
            patch("provider_gmail.Credentials") as creds_cls,
            patch("provider_gmail.InstalledAppFlow") as flow_cls,
            patch("provider_gmail.Request"),
        ):
            creds_cls.from_authorized_user_file.return_value = creds
            assert _exit_code_of_main_block() == 1

        flow_cls.from_client_secrets_file.assert_not_called()
        message = _messages_sent(requests_mock)[0]
        assert "GmailAuthError" in message and "expired or revoked" in message


# ---------------------------------------------------------------------------
# Tests: command-line flags (preview mode)
# ---------------------------------------------------------------------------

class TestCliFlags:
    def test_defaults(self):
        import main

        args = main.parse_args([])

        assert args.dry_run is False
        assert args.limit is None
        assert args.verbose is False

    def test_all_flags_parse(self):
        import main

        args = main.parse_args(["--dry-run", "--limit", "5", "--verbose"])

        assert args.dry_run is True
        assert args.limit == 5
        assert args.verbose is True

    @pytest.mark.parametrize("bad", [["--limit", "0"], ["--limit", "-3"], ["--limit", "x"]])
    def test_bad_limit_rejected(self, bad):
        import main

        with pytest.raises(SystemExit) as exc_info:
            main.parse_args(bad)

        assert exc_info.value.code == 2

    def test_apply_args_sets_dry_run_and_limit(self):
        import main

        settings = _settings()
        result = main.apply_args(settings, main.parse_args(["--dry-run", "--limit", "7"]))

        assert result.dry_run is True
        assert result.gmail_max_emails == 7

    def test_apply_args_without_flags_leaves_settings_unchanged(self):
        import main

        settings = _settings()

        assert main.apply_args(settings, main.parse_args([])) == settings


# ---------------------------------------------------------------------------
# Tests: --dry-run end to end
# ---------------------------------------------------------------------------

class TestDryRunEndToEnd:
    def test_dry_run_prints_briefing_and_never_calls_signal(self, requests_mock, capsys):
        """Real notifier + processor, mocked HTTP: only Ollama may be contacted."""
        import json

        import main

        requests_mock.get("http://localhost:11434/api/tags", json=_MODELS)
        requests_mock.get("http://localhost:11434/api/version", json={"version": "0.5.0"})
        requests_mock.post(
            "http://localhost:11434/api/generate",
            json={"response": json.dumps({"category": "urgent", "summary": "Reply to recruiter"})},
        )
        settings = _settings(dry_run=True)

        with patch("main.GmailProvider") as mock_gmail_cls:
            mock_gmail_cls.from_credentials.return_value.fetch_unread_emails.return_value = _sample_emails()
            main.run(settings)

        assert "Reply to recruiter" in capsys.readouterr().out
        assert {f"{r.hostname}:{r.port}" for r in requests_mock.request_history} == {"localhost:11434"}


    def test_ignored_email_is_absent_from_the_briefing(self, requests_mock, capsys):
        import json

        import main

        requests_mock.get("http://localhost:11434/api/tags", json=_MODELS)
        requests_mock.get("http://localhost:11434/api/version", json={"version": "0.5.0"})
        requests_mock.post(
            "http://localhost:11434/api/generate",
            json={"response": json.dumps({"category": "ignore", "summary": "Pills for cheap"})},
        )

        with patch("main.GmailProvider") as mock_gmail_cls:
            mock_gmail_cls.from_credentials.return_value.fetch_unread_emails.return_value = _sample_emails()
            main.run(_settings(dry_run=True))

        out = capsys.readouterr().out
        assert "Pills for cheap" not in out
        assert "All clear" in out


# ---------------------------------------------------------------------------
# Tests: end-of-run log messages
# ---------------------------------------------------------------------------

class TestCompletionLogging:
    def _log_of_run(self, caplog, settings) -> str:
        import main

        processor = MagicMock()
        processor.triage.return_value = TriageResult()
        with (
            patch("main.GmailProvider"),
            patch("main.AIProcessor", return_value=processor),
            patch("main.SignalNotifier"),
            caplog.at_level("INFO", logger="main"),
        ):
            main.run(settings)
        return caplog.text

    def test_real_run_reports_the_briefing_as_sent(self, caplog):
        assert "Briefing sent successfully." in self._log_of_run(caplog, _settings())

    def test_dry_run_does_not_claim_anything_was_sent(self, caplog):
        text = self._log_of_run(caplog, _settings(dry_run=True))

        assert "Sending Signal briefing" not in text
        assert "sent successfully" not in text
        assert "nothing sent" in text


# ---------------------------------------------------------------------------
# Tests: --verbose must not expose third-party DEBUG logging
# ---------------------------------------------------------------------------

_OWN_LOGGERS = ("__main__", "main", "provider_gmail", "processor_ai")
_THIRD_PARTY_LOGGERS = (
    "oauthlib.oauth2",
    "requests_oauthlib.oauth2_session",
    "google_auth_oauthlib.flow",
    "google.auth",
    "googleapiclient.discovery",
    "urllib3.connectionpool",
)


@pytest.fixture
def restore_log_levels():
    loggers = [logging.getLogger(n) for n in (*_OWN_LOGGERS, *_THIRD_PARTY_LOGGERS)]
    loggers.append(logging.getLogger())
    levels = [lg.level for lg in loggers]
    yield
    for lg, level in zip(loggers, levels):
        lg.setLevel(level)


@pytest.mark.usefixtures("restore_log_levels")
class TestVerboseLogging:
    @pytest.mark.parametrize("name", ["main", "provider_gmail", "processor_ai"])
    def test_own_loggers_emit_debug(self, name):
        import main

        main.enable_verbose_logging()

        assert logging.getLogger(name).isEnabledFor(logging.DEBUG)

    @pytest.mark.parametrize("name", _THIRD_PARTY_LOGGERS)
    def test_third_party_loggers_stay_quiet(self, name):
        import main

        main.enable_verbose_logging()

        assert not logging.getLogger(name).isEnabledFor(logging.DEBUG)

    def test_verbose_flag_is_wired_up_without_touching_the_root_logger(
        self, monkeypatch, requests_mock
    ):
        """Run the real __main__ block, so the flag's handling itself is covered."""
        _arrange_main_block(monkeypatch, "--verbose")
        _services_up(requests_mock)
        _gmail_fails_with(monkeypatch, RuntimeError("stop here"))

        _exit_code_of_main_block()

        assert logging.getLogger("processor_ai").isEnabledFor(logging.DEBUG)
        assert not logging.getLogger("oauthlib.oauth2").isEnabledFor(logging.DEBUG)
