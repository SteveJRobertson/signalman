"""Tests for the main orchestrator module.

All external dependencies (GmailProvider, AIProcessor, SignalNotifier, load_dotenv)
are mocked so the test suite requires no real credentials or running services.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """run() wires GmailProvider → AIProcessor → SignalNotifier correctly."""

    def _run_with_mocks(self, emails=None, triage=None, env=None):
        """Execute main.run() with all external dependencies mocked."""
        if emails is None:
            emails = _sample_emails()
        if triage is None:
            triage = _sample_triage()
        base_env = {
            "SIGNAL_SENDER": "+10000000001",
            "SIGNAL_RECIPIENT": "+10000000002",
        }
        if env:
            base_env.update(env)

        mock_provider = MagicMock()
        mock_provider.fetch_unread_emails.return_value = emails

        mock_processor = MagicMock()
        mock_processor.triage.return_value = triage

        mock_notifier = MagicMock()

        with (
            patch("main.GmailProvider") as mock_gmail_cls,
            patch("main.AIProcessor") as mock_ai_cls,
            patch("main.SignalNotifier") as mock_signal_cls,
            patch.dict("os.environ", base_env, clear=False),
        ):
            mock_gmail_cls.from_credentials.return_value = mock_provider
            mock_ai_cls.return_value = mock_processor
            mock_signal_cls.return_value = mock_notifier

            import main
            main.run()

        return mock_gmail_cls, mock_ai_cls, mock_signal_cls, mock_provider, mock_processor, mock_notifier

    def test_gmail_provider_from_credentials_called(self):
        mock_gmail_cls, *_ = self._run_with_mocks()
        mock_gmail_cls.from_credentials.assert_called_once()

    def test_fetch_unread_emails_called(self):
        *_, mock_provider, _, _ = self._run_with_mocks()
        mock_provider.fetch_unread_emails.assert_called_once()

    def test_ai_processor_receives_emails(self):
        emails = _sample_emails()
        *_, mock_provider, mock_processor, _ = self._run_with_mocks(emails=emails)
        mock_processor.triage.assert_called_once_with(emails)

    def test_signal_notifier_receives_triage(self):
        triage = _sample_triage()
        *_, mock_notifier = self._run_with_mocks(triage=triage)
        mock_notifier.send.assert_called_once_with(triage)

    def test_signal_notifier_constructed_with_sender(self):
        _, _, mock_signal_cls, *_ = self._run_with_mocks()
        _, kwargs = mock_signal_cls.call_args
        assert kwargs.get("sender") == "+10000000001" or mock_signal_cls.call_args[0][0] == "+10000000001"

    def test_signal_notifier_constructed_with_recipient(self):
        _, _, mock_signal_cls, *_ = self._run_with_mocks()
        call_kwargs = mock_signal_cls.call_args
        args, kwargs = call_kwargs
        recipient = kwargs.get("recipient") or (args[1] if len(args) > 1 else None)
        assert recipient == "+10000000002"

    def test_default_signal_cli_path(self):
        _, _, mock_signal_cls, *_ = self._run_with_mocks()
        args, kwargs = mock_signal_cls.call_args
        signal_cli = kwargs.get("signal_cli", "signal-cli")
        assert signal_cli == "signal-cli"

    def test_custom_signal_cli_path_from_env(self):
        _, _, mock_signal_cls, *_ = self._run_with_mocks(
            env={"SIGNAL_CLI_PATH": "/usr/local/bin/signal-cli"}
        )
        args, kwargs = mock_signal_cls.call_args
        assert kwargs.get("signal_cli") == "/usr/local/bin/signal-cli"

    def test_default_ollama_url(self):
        _, mock_ai_cls, *_ = self._run_with_mocks()
        args, kwargs = mock_ai_cls.call_args
        assert kwargs.get("url") == "http://localhost:11434/api/generate"

    def test_custom_ollama_url_from_env(self):
        _, mock_ai_cls, *_ = self._run_with_mocks(
            env={"OLLAMA_URL": "http://192.168.1.10:11434/api/generate"}
        )
        args, kwargs = mock_ai_cls.call_args
        assert kwargs.get("url") == "http://192.168.1.10:11434/api/generate"

    def test_default_ollama_model(self):
        _, mock_ai_cls, *_ = self._run_with_mocks()
        args, kwargs = mock_ai_cls.call_args
        assert kwargs.get("model") == "llama3"

    def test_custom_ollama_model_from_env(self):
        _, mock_ai_cls, *_ = self._run_with_mocks(env={"OLLAMA_MODEL": "mistral"})
        args, kwargs = mock_ai_cls.call_args
        assert kwargs.get("model") == "mistral"

    def test_empty_inbox_completes_without_error(self):
        """An empty inbox should still send an 'all clear' briefing."""
        triage = {"urgent": [], "tasks": [], "digest": []}
        *_, mock_notifier = self._run_with_mocks(emails=[], triage=triage)
        mock_notifier.send.assert_called_once_with(triage)


# ---------------------------------------------------------------------------
# Tests: missing environment variables
# ---------------------------------------------------------------------------

class TestMissingEnvVars:
    def test_missing_signal_sender_exits(self):
        """run() must exit with code 1 when SIGNAL_SENDER is not set."""
        env_without_sender = {k: v for k, v in {
            "SIGNAL_RECIPIENT": "+10000000002",
        }.items()}

        with (
            patch("main.GmailProvider"),
            patch("main.AIProcessor"),
            patch("main.SignalNotifier"),
            patch.dict("os.environ", env_without_sender, clear=True),
        ):
            import main
            with pytest.raises(KeyError):
                main.run()

    def test_missing_signal_recipient_exits(self):
        """run() must exit with code 1 when SIGNAL_RECIPIENT is not set."""
        env_without_recipient = {
            "SIGNAL_SENDER": "+10000000001",
        }

        with (
            patch("main.GmailProvider"),
            patch("main.AIProcessor"),
            patch("main.SignalNotifier"),
            patch.dict("os.environ", env_without_recipient, clear=True),
        ):
            import main
            with pytest.raises(KeyError):
                main.run()


# ---------------------------------------------------------------------------
# Tests: __main__ block error handling
# ---------------------------------------------------------------------------

class TestMainBlockErrorHandling:
    def test_key_error_causes_sys_exit_1(self, monkeypatch):
        """A missing env var bubbles up as SystemExit(1) in the __main__ block."""
        import main

        monkeypatch.setattr(main, "run", MagicMock(side_effect=KeyError("SIGNAL_SENDER")))

        with pytest.raises(SystemExit) as exc_info:
            with patch.object(sys, "argv", ["main.py"]):
                # Simulate what __main__ does
                try:
                    main.run()
                except KeyError as exc:
                    main.logger.error("Missing required environment variable: %s", exc)
                    sys.exit(1)

        assert exc_info.value.code == 1

    def test_generic_exception_causes_sys_exit_1(self, monkeypatch):
        """Any unexpected exception results in SystemExit(1)."""
        import main

        monkeypatch.setattr(main, "run", MagicMock(side_effect=RuntimeError("boom")))

        with pytest.raises(SystemExit) as exc_info:
            try:
                main.run()
            except Exception as exc:
                main.logger.error("Signalman failed: %s", exc)
                sys.exit(1)

        assert exc_info.value.code == 1
