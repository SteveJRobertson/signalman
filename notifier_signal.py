"""Signal notifier for Signalman.

Formats the AI-generated triage JSON into a human-readable message and
delivers it via signal-cli using a subprocess call.
"""

from __future__ import annotations

import subprocess
from typing import Sequence


class SignalNotifier:
    """Sends a formatted triage briefing to a Signal recipient.

    Args:
        sender:      The Signal account (phone number) used to send the message.
        recipient:   The phone number (or group ID) that will receive the message.
        signal_cli:  Path to the ``signal-cli`` executable (default: ``signal-cli``).
    """

    def __init__(
        self,
        sender: str,
        recipient: str,
        signal_cli: str = "signal-cli",
    ) -> None:
        self.sender = sender
        self.recipient = recipient
        self.signal_cli = signal_cli

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, triage: dict[str, list[str]]) -> None:
        """Format *triage* and deliver it via signal-cli.

        Args:
            triage: A dict with keys ``urgent``, ``tasks``, and ``digest``,
                    each mapping to a list of concise string descriptions
                    (as returned by :class:`~processor_ai.AIProcessor`).

        Raises:
            subprocess.CalledProcessError: If signal-cli exits with a non-zero
                status code (e.g. the Signal service is unavailable).
            FileNotFoundError: If the signal-cli executable cannot be found.
        """
        message = self.format_message(triage)
        cmd: Sequence[str] = [
            self.signal_cli,
            "-u", self.sender,
            "send",
            "-m", message,
            self.recipient,
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)

    @staticmethod
    def format_message(triage: dict[str, list[str]]) -> str:
        """Convert a triage dict into a clean, readable Signal message.

        Args:
            triage: A dict with keys ``urgent``, ``tasks``, and ``digest``.

        Returns:
            A multi-line string ready to be sent as a Signal message.
        """
        sections: list[str] = ["📬 *Signalman Daily Briefing*\n"]

        urgent = triage.get("urgent", [])
        tasks = triage.get("tasks", [])
        digest = triage.get("digest", [])

        if urgent:
            sections.append("🚨 *Urgent*")
            for item in urgent:
                sections.append(f"  • {item}")
            sections.append("")

        if tasks:
            sections.append("✅ *Tasks*")
            for item in tasks:
                sections.append(f"  • {item}")
            sections.append("")

        if digest:
            sections.append("📰 *Digest*")
            for item in digest:
                sections.append(f"  • {item}")
            sections.append("")

        if not urgent and not tasks and not digest:
            sections.append("All clear – no items to report today.")

        return "\n".join(sections).rstrip()
