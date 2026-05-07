"""Signal notifier for Signalman.

Formats the AI-generated triage JSON into a human-readable message and
delivers it via the Signal REST API running in a Docker container.
"""

from __future__ import annotations

import requests


class SignalNotifier:
    """Sends a formatted triage briefing to a Signal recipient.

    Args:
        sender:    The Signal account (phone number) used to send the message.
        recipient: The phone number that will receive the message.
        api_url:   Base URL of the Signal REST API (default: ``http://localhost:8080``).
    """

    def __init__(
        self,
        sender: str,
        recipient: str,
        api_url: str = "http://localhost:8080",
    ) -> None:
        self.sender = sender
        self.recipient = recipient
        self.api_url = api_url

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, triage: dict[str, list[str]]) -> None:
        """Format *triage* and deliver it via the Signal REST API.

        Args:
            triage: A dict with keys ``urgent``, ``tasks``, and ``digest``,
                    each mapping to a list of concise string descriptions
                    (as returned by :class:`~processor_ai.AIProcessor`).

        Raises:
            ConnectionError: If the Signal API Docker container is unreachable.
            requests.HTTPError: If the API returns a non-2xx response.
        """
        self._check_reachable()
        message = self.format_message(triage)
        payload = {
            "message": message,
            "number": self.sender,
            "recipients": [self.recipient],
        }
        response = requests.post(
            f"{self.api_url}/v2/send",
            json=payload,
            timeout=30,
        )
        response.raise_for_status()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_reachable(self) -> None:
        """Verify the Signal API container is reachable before sending.

        Raises:
            ConnectionError: If the container cannot be reached.
        """
        try:
            requests.get(f"{self.api_url}/v1/about", timeout=3)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            raise ConnectionError(
                f"Signal API container is unreachable at {self.api_url}. "
                "Make sure the Docker container is running."
            ) from exc

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
