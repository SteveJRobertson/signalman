"""Signalman orchestrator.

Loads settings from the environment, fetches unread emails via
GmailProvider, triages them with AIProcessor, and delivers the daily
briefing via SignalNotifier.

Usage::

    python main.py

See ``config.Settings`` for the full list of environment variables.
"""

from __future__ import annotations

import logging
import sys

from dotenv import load_dotenv

from config import Settings
from notifier_signal import SignalNotifier
from processor_ai import AIProcessor
from provider_gmail import GmailProvider

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def run(settings: Settings) -> None:
    """Fetch emails, triage them, and send the daily briefing via Signal."""
    logger.info("Fetching unread emails from Gmail…")
    provider = GmailProvider.from_credentials(settings)
    emails = provider.fetch_unread_emails()
    logger.info("Fetched %d email(s).", len(emails))

    logger.info("Triaging emails with AI processor…")
    processor = AIProcessor(settings)
    triage = processor.triage(emails)
    logger.info(
        "Triage complete – urgent=%d, tasks=%d, digest=%d.",
        len(triage["urgent"]),
        len(triage["tasks"]),
        len(triage["digest"]),
    )

    logger.info("Sending Signal briefing…")
    notifier = SignalNotifier(settings)
    notifier.send(triage)
    logger.info("Briefing sent successfully.")


if __name__ == "__main__":
    try:
        run(Settings.from_env())
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.error("Signalman failed: %s", exc)
        sys.exit(1)
