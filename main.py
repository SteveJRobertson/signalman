"""Signalman orchestrator.

Loads credentials from .env, fetches unread emails via GmailProvider,
triages them with AIProcessor, and delivers the daily briefing via
SignalNotifier.

Usage::

    python main.py

Required environment variables (set in ``.env`` or the shell):

- ``SIGNAL_SENDER``      – The Signal phone number registered on this device.
- ``SIGNAL_RECIPIENT``   – The phone number that receives the briefing.

Optional environment variables:

- ``GMAIL_TOKEN_PATH``       – Path to the OAuth2 token file (default: ``token.json``).
- ``GMAIL_CREDENTIALS_PATH`` – Path to the OAuth2 credentials file (default: ``credentials.json``).
- ``SIGNAL_CLI_PATH``        – Path to the ``signal-cli`` executable (default: ``signal-cli``).
- ``OLLAMA_URL``             – Ollama API endpoint (default: ``http://localhost:11434/api/generate``).
- ``OLLAMA_MODEL``           – Model name to use for triage (default: ``llama3``).
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

from notifier_signal import SignalNotifier
from processor_ai import AIProcessor
from provider_gmail import GmailProvider

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def run() -> None:
    """Fetch emails, triage them, and send the daily briefing via Signal."""
    signal_sender = os.environ["SIGNAL_SENDER"]
    signal_recipient = os.environ["SIGNAL_RECIPIENT"]
    signal_cli = os.getenv("SIGNAL_CLI_PATH", "signal-cli")

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    ollama_model = os.getenv("OLLAMA_MODEL", "llama3")

    logger.info("Fetching unread emails from Gmail…")
    provider = GmailProvider.from_credentials()
    emails = provider.fetch_unread_emails()
    logger.info("Fetched %d email(s).", len(emails))

    logger.info("Triaging emails with AI processor…")
    processor = AIProcessor(url=ollama_url, model=ollama_model)
    triage = processor.triage(emails)
    logger.info(
        "Triage complete – urgent=%d, tasks=%d, digest=%d.",
        len(triage["urgent"]),
        len(triage["tasks"]),
        len(triage["digest"]),
    )

    logger.info("Sending Signal briefing…")
    notifier = SignalNotifier(
        sender=signal_sender,
        recipient=signal_recipient,
        signal_cli=signal_cli,
    )
    notifier.send(triage)
    logger.info("Briefing sent successfully.")


if __name__ == "__main__":
    try:
        run()
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.error("Signalman failed: %s", exc)
        sys.exit(1)
