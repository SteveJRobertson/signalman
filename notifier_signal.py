"""Signal notifier for Signalman.

Formats a :class:`~processor_ai.TriageResult` into a human-readable message
and delivers it via the Signal REST API running in a Docker container.
"""

from __future__ import annotations

import requests

from config import Settings
from processor_ai import TriageResult


class SignalNotifier:
    """Sends a formatted triage briefing to a Signal recipient."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sender = settings.signal_sender
        self.recipient = settings.signal_recipient
        self.api_url = settings.signal_api_url

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, result: TriageResult) -> None:
        """Format *result* and deliver it via the Signal REST API.

        Args:
            result: The triage verdicts, as returned by
                :meth:`~processor_ai.AIProcessor.triage`.

        In dry-run mode (``settings.dry_run``) the message is printed to
        stdout and no HTTP request is made.

        Raises:
            ConnectionError: If the Signal API Docker container is unreachable.
            requests.HTTPError: If the API returns a non-2xx response.
        """
        message = self.format_message(result)
        if self.settings.dry_run:
            print(message)
            return
        self._check_reachable()
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
            ConnectionError: If the container cannot be reached or returns a
                non-2xx response.
        """
        try:
            response = requests.get(f"{self.api_url}/v1/about", timeout=3)
            response.raise_for_status()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            raise ConnectionError(
                f"Signal API container is unreachable at {self.api_url}. "
                "Make sure the Docker container is running."
            ) from exc
        except requests.exceptions.HTTPError as exc:
            raise ConnectionError(
                f"Signal API container returned an unexpected status at {self.api_url}: {exc}"
            ) from exc

    @staticmethod
    def format_message(result: TriageResult) -> str:
        """Convert a :class:`TriageResult` into a clean, readable Signal message.

        ``ignore`` items are never shown. Emails that could not be classified
        are called out explicitly: a silently short briefing is
        indistinguishable from a quiet inbox.

        Returns:
            A multi-line string ready to be sent as a Signal message.
        """
        sections: list[str] = ["📬 *Signalman Daily Briefing*\n"]
        shown = False

        for heading, category in (
            ("🚨 *Urgent*", "urgent"),
            ("✅ *Tasks*", "tasks"),
            ("📰 *Digest*", "digest"),
        ):
            items = result.by_category(category)
            if not items:
                continue
            shown = True
            sections.append(heading)
            sections.extend(f"  • {item.summary}" for item in items)
            sections.append("")

        if result.failed_ids:
            count = len(result.failed_ids)
            noun = "email" if count == 1 else "emails"
            sections.append(f"⚠️ {count} {noun} could not be classified")
        elif not shown:
            sections.append("All clear – no items to report today.")

        return "\n".join(sections).rstrip()
