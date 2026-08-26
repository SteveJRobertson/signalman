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

    def _run_with_mocks(self, emails=None, triage=None, settings=None, limit=None):
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
            main.run(settings, limit=limit)

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

    def test_limit_truncates_emails_before_triage(self):
        """--limit caps the emails passed to the processor, not just fetched."""
        emails = [
            {"id": "msg1", "subject": "One", "sender": "a@example.com", "body": ""},
            {"id": "msg2", "subject": "Two", "sender": "b@example.com", "body": ""},
            {"id": "msg3", "subject": "Three", "sender": "c@example.com", "body": ""},
        ]
        mocks = self._run_with_mocks(emails=emails, limit=1)
        mocks["processor"].triage.assert_called_once_with([emails[0]])

    def test_no_limit_processes_all_emails(self):
        """Without --limit, every fetched email reaches the processor."""
        emails = _sample_emails()
        mocks = self._run_with_mocks(emails=emails, limit=None)
        mocks["processor"].triage.assert_called_once_with(emails)

    def test_limit_larger_than_inbox_is_a_no_op(self):
        """A limit greater than the number of emails changes nothing."""
        emails = _sample_emails()
        mocks = self._run_with_mocks(emails=emails, limit=100)
        mocks["processor"].triage.assert_called_once_with(emails)


# ---------------------------------------------------------------------------
# Tests: CLI argument parsing
# ---------------------------------------------------------------------------

class TestArgParser:
    """build_arg_parser() defines the preview-mode flags."""

    def _parse(self, argv):
        import main
        return main.build_arg_parser().parse_args(argv)

    def test_defaults(self):
        args = self._parse([])
        assert args.dry_run is False
        assert args.limit is None
        assert args.verbose is False

    def test_dry_run_flag(self):
        assert self._parse(["--dry-run"]).dry_run is True

    def test_limit_flag_parses_as_int(self):
        args = self._parse(["--limit", "5"])
        assert args.limit == 5
        assert isinstance(args.limit, int)

    def test_verbose_flag(self):
        assert self._parse(["--verbose"]).verbose is True

    def test_flags_combine(self):
        args = self._parse(["--dry-run", "--limit", "3", "--verbose"])
        assert args.dry_run is True
        assert args.limit == 3
        assert args.verbose is True


# ---------------------------------------------------------------------------
# Tests: __main__ block CLI wiring
# ---------------------------------------------------------------------------
#
# TestArgParser proves the flags parse correctly; TestRunSuccess proves
# run(settings, limit=...) does the right thing with them. This test covers
# the one seam neither of those reaches: the two lines in __main__ itself
# that turn --dry-run into a Settings.dry_run=True actually passed to
# SignalNotifier. Runs the real module via runpy, in the same style as
# TestMainBlockErrorHandling below — patching the shared provider_gmail /
# processor_ai / notifier_signal module attributes (not "main.X") is
# required here, because runpy re-executes main's top-level imports fresh
# and would not see a patch applied to main's own namespace.

class TestMainBlockCLIWiring:
    def test_dry_run_flag_produces_dry_run_settings(self, monkeypatch):
        import runpy
        import provider_gmail
        import processor_ai
        import notifier_signal

        monkeypatch.setattr(sys, "argv", ["main.py", "--dry-run"])
        monkeypatch.setenv("SIGNAL_SENDER_NUMBER", "+10000000001")
        monkeypatch.setenv("SIGNAL_RECIPIENT_NUMBER", "+10000000002")

        mock_provider = MagicMock()
        mock_provider.fetch_unread_emails.return_value = []
        monkeypatch.setattr(
            provider_gmail.GmailProvider,
            "from_credentials",
            MagicMock(return_value=mock_provider),
        )

        mock_processor = MagicMock()
        mock_processor.triage.return_value = {"urgent": [], "tasks": [], "digest": []}
        monkeypatch.setattr(processor_ai, "AIProcessor", MagicMock(return_value=mock_processor))

        mock_notifier_cls = MagicMock()
        monkeypatch.setattr(notifier_signal, "SignalNotifier", mock_notifier_cls)

        runpy.run_module("main", run_name="__main__", alter_sys=True)

        constructed_settings = mock_notifier_cls.call_args[0][0]
        assert constructed_settings.dry_run is True


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
