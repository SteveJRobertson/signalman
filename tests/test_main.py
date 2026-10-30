"""Tests for the main orchestrator module.

All external dependencies (GmailProvider, AIProcessor, SignalNotifier, load_dotenv)
are mocked so the test suite requires no real credentials or running services.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from config import Settings


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


def _sample_emails() -> list[dict]:
    return [
        {
            "id": "msg1",
            "subject": "Interview invite – please respond today",
            "sender": "recruiter@techcorp.com",
            "body": "Confirm your availability by end of day.",
        }
    ]


def _sample_triage() -> dict:
    return {
        "urgent": ["Interview invite from techcorp.com – confirm today"],
        "tasks": ["Return school trip permission slip by Friday"],
        "digest": ["Weekly newsletter summary"],
    }


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
        triage = {"urgent": [], "tasks": [], "digest": []}
        mocks = self._run_with_mocks(emails=[], triage=triage)
        mocks["notifier"].send.assert_called_once_with(triage)


# ---------------------------------------------------------------------------
# Tests: __main__ block error handling
# ---------------------------------------------------------------------------
#
# Environment-driven failures (missing required vars, bad numeric/URL values)
# are exercised against config.Settings.from_env() directly in
# tests/test_config.py. These tests instead confirm the real __main__
# entrypoint in main.py turns those failures — and any other unexpected
# exception — into a clean SystemExit(1), by running the actual module via
# runpy rather than mocking main.run().

class TestMainBlockErrorHandling:
    def test_key_error_causes_sys_exit_1(self, monkeypatch):
        """A missing env var causes SystemExit(1) via the real __main__ handler."""
        import runpy

        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        monkeypatch.setattr(sys, "argv", ["main.py"])
        monkeypatch.delenv("SIGNAL_SENDER_NUMBER", raising=False)
        monkeypatch.delenv("SIGNAL_RECIPIENT_NUMBER", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("main", run_name="__main__", alter_sys=True)

        assert exc_info.value.code == 1

    def test_generic_exception_causes_sys_exit_1(self, monkeypatch):
        """Any unexpected exception causes SystemExit(1) via the real __main__ handler."""
        import runpy
        import provider_gmail

        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        monkeypatch.setattr(sys, "argv", ["main.py"])
        monkeypatch.setenv("SIGNAL_SENDER_NUMBER", "+10000000001")
        monkeypatch.setenv("SIGNAL_RECIPIENT_NUMBER", "+10000000002")
        monkeypatch.setattr(
            provider_gmail.GmailProvider,
            "from_credentials",
            MagicMock(side_effect=RuntimeError("boom")),
        )

        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("main", run_name="__main__", alter_sys=True)

        assert exc_info.value.code == 1


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

        requests_mock.post(
            "http://localhost:11434/api/generate",
            json={"response": json.dumps({"urgent": ["Reply to recruiter"], "tasks": [], "digest": []})},
        )
        settings = _settings(dry_run=True)

        with patch("main.GmailProvider") as mock_gmail_cls:
            mock_gmail_cls.from_credentials.return_value.fetch_unread_emails.return_value = _sample_emails()
            main.run(settings)

        assert "Reply to recruiter" in capsys.readouterr().out
        assert [r.hostname + ":" + str(r.port) for r in requests_mock.request_history] == [
            "localhost:11434"
        ]
