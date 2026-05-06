"""Tests for the Signal notifier module.

All subprocess calls are mocked so no running signal-cli instance is required.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from notifier_signal import SignalNotifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_triage() -> dict:
    return {
        "urgent": ["Interview invite from techcorp.com – confirm availability today"],
        "tasks": ["Return school trip permission slip by Friday"],
        "digest": ["Weekly newsletter: top stories summary"],
    }


def _empty_triage() -> dict:
    return {"urgent": [], "tasks": [], "digest": []}


# ---------------------------------------------------------------------------
# Tests: SignalNotifier initialisation
# ---------------------------------------------------------------------------

class TestSignalNotifierInit:
    def test_stores_sender(self):
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        assert notifier.sender == "+10000000001"

    def test_stores_recipient(self):
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        assert notifier.recipient == "+10000000002"

    def test_default_signal_cli_path(self):
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        assert notifier.signal_cli == "signal-cli"

    def test_custom_signal_cli_path(self):
        notifier = SignalNotifier(
            sender="+10000000001",
            recipient="+10000000002",
            signal_cli="/usr/local/bin/signal-cli",
        )
        assert notifier.signal_cli == "/usr/local/bin/signal-cli"


# ---------------------------------------------------------------------------
# Tests: format_message
# ---------------------------------------------------------------------------

class TestFormatMessage:
    def test_contains_header(self):
        message = SignalNotifier.format_message(_sample_triage())
        assert "Signalman Daily Briefing" in message

    def test_urgent_section_present(self):
        message = SignalNotifier.format_message(_sample_triage())
        assert "Urgent" in message
        assert "Interview invite" in message

    def test_tasks_section_present(self):
        message = SignalNotifier.format_message(_sample_triage())
        assert "Tasks" in message
        assert "school trip" in message

    def test_digest_section_present(self):
        message = SignalNotifier.format_message(_sample_triage())
        assert "Digest" in message
        assert "Weekly newsletter" in message

    def test_all_clear_when_empty(self):
        message = SignalNotifier.format_message(_empty_triage())
        assert "All clear" in message

    def test_urgent_section_omitted_when_empty(self):
        triage = {"urgent": [], "tasks": ["Do something"], "digest": []}
        message = SignalNotifier.format_message(triage)
        assert "Urgent" not in message

    def test_tasks_section_omitted_when_empty(self):
        triage = {"urgent": ["Fix now"], "tasks": [], "digest": []}
        message = SignalNotifier.format_message(triage)
        assert "Tasks" not in message

    def test_digest_section_omitted_when_empty(self):
        triage = {"urgent": [], "tasks": [], "digest": []}
        message = SignalNotifier.format_message(triage)
        assert "Digest" not in message

    def test_multiple_urgent_items_all_present(self):
        triage = {
            "urgent": ["First urgent item", "Second urgent item"],
            "tasks": [],
            "digest": [],
        }
        message = SignalNotifier.format_message(triage)
        assert "First urgent item" in message
        assert "Second urgent item" in message

    def test_items_formatted_as_bullet_points(self):
        triage = {"urgent": ["Critical fix"], "tasks": [], "digest": []}
        message = SignalNotifier.format_message(triage)
        assert "• Critical fix" in message


# ---------------------------------------------------------------------------
# Tests: send – successful subprocess call
# ---------------------------------------------------------------------------

class TestSendSuccess:
    def test_subprocess_run_called_on_send(self):
        """A successful send invokes subprocess.run exactly once."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifier.send(_sample_triage())

        mock_run.assert_called_once()

    def test_subprocess_run_uses_check_true(self):
        """subprocess.run is called with check=True so errors raise exceptions."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifier.send(_sample_triage())

        _, kwargs = mock_run.call_args
        assert kwargs.get("check") is True

    def test_command_includes_sender(self):
        """The signal-cli command includes the sender account with -u flag."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifier.send(_sample_triage())

        cmd = mock_run.call_args[0][0]
        assert "-u" in cmd
        assert "+10000000001" in cmd

    def test_command_includes_recipient(self):
        """The signal-cli command includes the recipient phone number."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifier.send(_sample_triage())

        cmd = mock_run.call_args[0][0]
        assert "+10000000002" in cmd

    def test_command_includes_send_subcommand(self):
        """The signal-cli command includes the 'send' subcommand."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifier.send(_sample_triage())

        cmd = mock_run.call_args[0][0]
        assert "send" in cmd

    def test_command_includes_message_flag(self):
        """The signal-cli command passes the formatted message with -m flag."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifier.send(_sample_triage())

        cmd = mock_run.call_args[0][0]
        assert "-m" in cmd

    def test_command_message_contains_triage_content(self):
        """The message passed to signal-cli contains content from the triage dict."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifier.send(_sample_triage())

        cmd = mock_run.call_args[0][0]
        message_index = cmd.index("-m") + 1
        message = cmd[message_index]
        assert "Interview invite" in message
        assert "school trip" in message
        assert "Weekly newsletter" in message

    def test_custom_signal_cli_path_used_in_command(self):
        """A custom signal-cli path is used as the first element of the command."""
        notifier = SignalNotifier(
            sender="+10000000001",
            recipient="+10000000002",
            signal_cli="/opt/signal-cli/bin/signal-cli",
        )

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            notifier.send(_sample_triage())

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/opt/signal-cli/bin/signal-cli"


# ---------------------------------------------------------------------------
# Tests: send – error handling
# ---------------------------------------------------------------------------

class TestSendErrorHandling:
    def test_called_process_error_propagates(self):
        """If signal-cli exits non-zero, CalledProcessError is raised."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd="signal-cli"
            )
            with pytest.raises(subprocess.CalledProcessError):
                notifier.send(_sample_triage())

    def test_file_not_found_propagates(self):
        """If signal-cli is not installed, FileNotFoundError is raised."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("signal-cli not found")
            with pytest.raises(FileNotFoundError):
                notifier.send(_sample_triage())

    def test_service_down_raises_called_process_error(self):
        """A non-zero exit code from signal-cli (e.g. service down) raises CalledProcessError."""
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")

        with patch("notifier_signal.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=2,
                cmd="signal-cli",
                stderr="Error: Failed to connect to Signal service",
            )
            with pytest.raises(subprocess.CalledProcessError) as exc_info:
                notifier.send(_sample_triage())

        assert exc_info.value.returncode == 2
