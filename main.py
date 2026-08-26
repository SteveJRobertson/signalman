"""Signalman orchestrator.

Loads settings from the environment, fetches unread emails via
GmailProvider, triages them with AIProcessor, and delivers the daily
briefing via SignalNotifier.

Usage::

    python main.py [--dry-run] [--limit N] [--verbose]

See ``config.Settings`` for the full list of environment variables.
"""

from __future__ import annotations

import argparse
import dataclasses
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


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser for preview-mode flags."""
    parser = argparse.ArgumentParser(
        description="Signalman: AI-triaged Gmail briefing delivered over Signal."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the briefing to stdout instead of sending it via Signal.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N unread emails (for fast iteration).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging, including each AI prompt and raw reply.",
    )
    return parser


def run(settings: Settings, *, limit: int | None = None) -> None:
    """Fetch emails, triage them, and send the daily briefing via Signal."""
    logger.info("Fetching unread emails from Gmail…")
    provider = GmailProvider.from_credentials(settings)
    emails = provider.fetch_unread_emails()
    logger.info("Fetched %d email(s).", len(emails))

    if limit is not None and len(emails) > limit:
        logger.info("Limiting to the first %d email(s) (--limit).", limit)
        emails = emails[:limit]

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
    args = build_arg_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        settings = Settings.from_env()
        if args.dry_run:
            settings = dataclasses.replace(settings, dry_run=True)
        run(settings, limit=args.limit)
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.error("Signalman failed: %s", exc)
        sys.exit(1)
