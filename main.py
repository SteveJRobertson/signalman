"""Signalman orchestrator.

Loads settings from the environment, fetches unread emails via
GmailProvider, triages them with AIProcessor, and delivers the daily
briefing via SignalNotifier.

Usage::

    python main.py                  # real run
    python main.py --dry-run        # print the briefing, send nothing
    python main.py --limit 10       # process at most 10 emails
    python main.py --verbose        # DEBUG logging, incl. prompts and raw replies

See ``config.Settings`` for the full list of environment variables.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys

from dotenv import load_dotenv

from cleaner import clean
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line flags for preview mode."""
    parser = argparse.ArgumentParser(
        prog="signalman",
        description="Triage unread Gmail with a local LLM and send a Signal briefing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the briefing to stdout instead of sending it via Signal",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="process at most N emails (useful for fast iteration)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="DEBUG logging, including each AI prompt and raw reply",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def apply_args(settings: Settings, args: argparse.Namespace) -> Settings:
    """Return *settings* with any command-line overrides applied."""
    overrides: dict[str, object] = {}
    if args.dry_run:
        overrides["dry_run"] = True
    if args.limit is not None:
        overrides["gmail_max_emails"] = args.limit
    return dataclasses.replace(settings, **overrides)


def run(settings: Settings) -> None:
    """Fetch emails, triage them, and send the daily briefing via Signal."""
    logger.info("Fetching unread emails from Gmail…")
    provider = GmailProvider.from_credentials(settings)
    emails = provider.fetch_unread_emails()
    logger.info("Fetched %d email(s).", len(emails))

    emails = [
        {**email, "body": clean(email["body"], max_chars=settings.max_body_chars)}
        for email in emails
    ]

    logger.info("Triaging emails with AI processor…")
    processor = AIProcessor(settings)
    result = processor.triage(emails)
    logger.info(
        "Triage complete – urgent=%d, tasks=%d, digest=%d, ignored=%d, failed=%d.",
        len(result.by_category("urgent")),
        len(result.by_category("tasks")),
        len(result.by_category("digest")),
        len(result.by_category("ignore")),
        len(result.failed_ids),
    )

    logger.info("Sending Signal briefing…")
    notifier = SignalNotifier(settings)
    notifier.send(result)
    logger.info("Briefing sent successfully.")


if __name__ == "__main__":
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    try:
        run(apply_args(Settings.from_env(), args))
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        logger.error("Signalman failed: %s", exc)
        sys.exit(1)
