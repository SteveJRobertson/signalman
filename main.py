"""Signalman orchestrator.

Loads settings from the environment, waits for Ollama and Signal to be ready,
fetches unread emails via GmailProvider, triages them with AIProcessor, and
delivers the daily briefing via SignalNotifier. If anything fails after that
first step, a Signal alert says what went wrong.

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
import time

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

# Never the root logger: at DEBUG, Google's auth libraries log OAuth tokens.
_VERBOSE_LOGGERS = (__name__, "provider_gmail", "processor_ai")


def enable_verbose_logging() -> None:
    """Turn on DEBUG for Signalman's own loggers, leaving third-party ones alone."""
    for name in _VERBOSE_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)


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


def wait_until_ready(settings: Settings, processor: AIProcessor, notifier: SignalNotifier) -> None:
    """Poll the services a run depends on until they all answer.

    At 08:00 after a restart Ollama and Docker are routinely still starting, so
    waiting here turns the most common real failure into a non-event. A dry run
    never contacts Signal, so it does not wait for it either.

    Raises:
        ConnectionError: If anything is still not ready after the last attempt.
        processor_ai.ModelNotFoundError: At once if the model is not installed,
            since no amount of waiting will install it.
    """
    checks = [processor.check_ready]
    if not settings.dry_run:
        checks.append(notifier.check_ready)

    attempts = settings.readiness_attempts
    for attempt in range(1, attempts + 1):
        problems: list[str] = []
        for check in checks:
            try:
                check()
            except ConnectionError as exc:
                problems.append(str(exc))
        if not problems:
            return
        if attempt < attempts:
            logger.info(
                "Not ready (attempt %d/%d): %s Retrying in %ds…",
                attempt,
                attempts,
                " ".join(problems),
                settings.readiness_interval,
            )
            time.sleep(settings.readiness_interval)

    raise ConnectionError(f"Still not ready after {attempts} attempts: {' '.join(problems)}")


def run(settings: Settings) -> None:
    """Check the services are up, fetch and triage emails, and send the briefing."""
    processor = AIProcessor(settings)
    notifier = SignalNotifier(settings)
    wait_until_ready(settings, processor, notifier)

    logger.info("Fetching unread emails from Gmail…")
    provider = GmailProvider.from_credentials(settings)
    emails = provider.fetch_unread_emails()
    logger.info("Fetched %d email(s).", len(emails))

    emails = [
        {**email, "body": clean(email["body"], max_chars=settings.max_body_chars)}
        for email in emails
    ]

    logger.info("Triaging emails with AI processor…")
    result = dataclasses.replace(
        processor.triage(emails), fetch_failed_ids=tuple(provider.failed_ids)
    )
    logger.info(
        "Triage complete – urgent=%d, tasks=%d, digest=%d, ignored=%d, failed=%d.",
        len(result.by_category("urgent")),
        len(result.by_category("tasks")),
        len(result.by_category("digest")),
        len(result.by_category("ignore")),
        len(result.failed_ids),
    )

    logger.info("Printing briefing (dry run)…" if settings.dry_run else "Sending Signal briefing…")
    notifier.send(result)
    logger.info("Dry run complete; nothing sent." if settings.dry_run else "Briefing sent successfully.")


def report_failure(settings: Settings, error: Exception) -> None:
    """Try to tell the user that the run failed. Never raises.

    If the alert fails as well there is nothing further to try: the day's
    missing briefing is then the only signal, which is why it is sent daily.
    """
    try:
        SignalNotifier(settings).send_alert(error)
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not send the failure alert: %s: %s", type(exc).__name__, exc)


if __name__ == "__main__":
    args = parse_args()
    if args.verbose:
        enable_verbose_logging()

    # Apart from the run itself: until settings exist there is nobody to alert,
    # and a KeyError from later on does not mean a variable is missing.
    try:
        settings = apply_args(Settings.from_env(), args)
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)
    except ValueError as exc:
        logger.error("Invalid configuration: %s", exc)
        sys.exit(1)

    try:
        run(settings)
    except Exception as exc:  # noqa: BLE001
        logger.error("Signalman failed: %s: %s", type(exc).__name__, exc)
        report_failure(settings, exc)
        sys.exit(1)
