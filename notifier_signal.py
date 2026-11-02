"""Signal notifier for Signalman.

Formats a :class:`~processor_ai.TriageResult` into a human-readable message
and delivers it via the Signal REST API running in a Docker container. Also
delivers the alert sent when a run fails.
"""

from __future__ import annotations

from datetime import datetime

import requests

from config import Settings
from processor_ai import TriageResult

_MAX_DIGEST_ITEMS = 20
_MAX_ALERT_DETAIL_CHARS = 500


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
        self.check_ready()
        self._post(message)

    def send_alert(self, error: Exception) -> None:
        """Tell the user a run failed, printing it instead in dry-run mode.

        There is no reachability probe first: the caller is already handling a
        failure, and the POST itself says whether the alert got through.

        Raises:
            requests.RequestException: If the alert could not be delivered.
        """
        message = self.format_alert(error)
        if self.settings.dry_run:
            print(message)
            return
        self._post(message)

    def check_ready(self) -> None:
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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _post(self, message: str) -> None:
        """Deliver *message* to the recipient via the Signal REST API."""
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

    @staticmethod
    def format_alert(error: Exception, *, now: datetime | None = None) -> str:
        """Convert a failure into the alert: what went wrong, and when."""
        detail = " ".join(str(error).split())[:_MAX_ALERT_DETAIL_CHARS]
        timestamp = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
        return f"🔴 Signalman failed\n{type(error).__name__}: {detail}\n{timestamp}"

    @staticmethod
    def format_message(result: TriageResult) -> str:
        """Convert a :class:`TriageResult` into a clean, readable Signal message.

        ``ignore`` items are never shown. Emails that could not be fetched or
        classified are called out explicitly: a silently short briefing is
        indistinguishable from a quiet inbox. The digest is capped, but urgent
        and tasks never are.

        An empty result still produces a message, and that is deliberate. The
        daily briefing doubles as a heartbeat: no message by 09:00 means
        something is wrong, even when Signal itself is what broke. Do not
        optimise it away.

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
            listed = items[:_MAX_DIGEST_ITEMS] if category == "digest" else items
            sections.extend(f"  • {item.summary}" for item in listed)
            if len(listed) < len(items):
                sections.append(f"  …and {len(items) - len(listed)} more")
            sections.append("")

        failures = (
            (result.fetch_failed_ids, "could not be fetched"),
            (result.failed_ids, "could not be classified"),
        )
        for ids, problem in failures:
            if ids:
                noun = "email" if len(ids) == 1 else "emails"
                sections.append(f"⚠️ {len(ids)} {noun} {problem}")

        if not shown and not result.fetch_failed_ids and not result.failed_ids:
            sections.append("All clear – no items to report today.")

        return "\n".join(sections).rstrip()
