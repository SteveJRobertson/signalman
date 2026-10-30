"""AI processor for Signalman.

Classifies each email with a local Ollama LLM — one call per email — and
returns a :class:`TriageResult` mapping every verdict back to the email that
produced it. Each email lands in exactly one category:

- ``urgent``: needs a reply or action today.
- ``tasks``:  contains an action item, but not time-critical today.
- ``digest``: low-priority information worth a skim.
- ``ignore``: junk or automated noise; filtered out before the briefing.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

import requests

from config import Settings

logger = logging.getLogger(__name__)

Category = Literal["urgent", "tasks", "digest", "ignore"]
CATEGORIES: tuple[str, ...] = get_args(Category)

_MAX_SUBJECT_CHARS = 200
_MAX_SENDER_CHARS = 100


class ClassificationError(Exception):
    """The model's verdict for one email could not be turned into a result."""


@dataclass(frozen=True, slots=True)
class TriagedItem:
    """The verdict for a single email."""

    email_id: str
    sender: str
    subject: str
    category: Category
    summary: str


@dataclass(frozen=True, slots=True)
class TriageResult:
    """Everything the notifier needs: verdicts, failures, and a skip count."""

    items: tuple[TriagedItem, ...] = ()
    failed_ids: tuple[str, ...] = ()  # classification failed
    skipped_count: int = 0  # already reported on a previous day (Phase 7)

    def by_category(self, category: Category) -> list[TriagedItem]:
        """Return the items in *category*, in the order they were triaged."""
        return [item for item in self.items if item.category == category]


_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an email triage assistant. You will be given one email.
    Classify it into exactly one of four categories:

    - urgent: Emails that require an immediate response today. This includes anything related to
              job applications (interview invites, offer deadlines, recruiter follow-ups),
              children's school communications as a parent (e.g. paying for milk or school lunches,
              upcoming assemblies, school trips, packed lunch reminders, permission slips),
              and any email that explicitly asks for a reply or action by end of day.
    - tasks:  Emails that contain clear action items but are not time-critical today
              (e.g. follow-ups, requests with a future deadline, things to schedule).
    - digest: Low-priority emails such as newsletters, FYIs, marketing, or general
              information that need no immediate action.
    - ignore: Junk, spam, or automated notifications that require no action and are
              not worth a mention.

    Also write a concise one-line summary of the email (what it is and what, if anything,
    is needed).

    Respond with ONLY valid JSON in the following format, with no additional text:
    {"category": "urgent" | "tasks" | "digest" | "ignore", "summary": "<concise one-line summary>"}
""")


class AIProcessor:
    """Triages emails using a local Ollama LLM, one call per email."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = settings.ollama_model
        self.generate_url = f"{settings.ollama_url}/api/generate"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def triage(self, emails: list[dict]) -> TriageResult:
        """Classify each of *emails* and return the combined result.

        Emails are processed sequentially. An email whose verdict cannot be
        parsed is recorded in ``TriageResult.failed_ids`` and the run
        continues; transport failures (Ollama down, HTTP errors) are not
        swallowed — they abort the run.

        Args:
            emails: Email dicts with keys ``id``, ``subject``, ``sender`` and
                ``body`` (as returned by :class:`~provider_gmail.GmailProvider`).

        Raises:
            requests.RequestException: If the Ollama API is unreachable or
                returns a non-2xx status.
        """
        items: list[TriagedItem] = []
        failed: list[str] = []

        for position, email in enumerate(emails, start=1):
            logger.info("Classifying %d/%d…", position, len(emails))
            try:
                items.append(self._classify_one(email))
            except ClassificationError as exc:
                logger.warning("Could not classify email %r: %s", email.get("id", ""), exc)
                failed.append(email.get("id", ""))

        return TriageResult(items=tuple(items), failed_ids=tuple(failed))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _classify_one(self, email: dict) -> TriagedItem:
        """Ask the model for a verdict on a single email.

        Raises:
            ClassificationError: If the reply is not a usable verdict.
        """
        raw = self._call_ollama(self._build_prompt(email))
        category, summary = self._parse_response(raw, fallback_summary=email.get("subject", ""))
        return TriagedItem(
            email_id=email.get("id", ""),
            sender=email.get("sender", ""),
            subject=email.get("subject", ""),
            category=category,
            summary=summary,
        )

    @staticmethod
    def _build_prompt(email: dict) -> str:
        """Construct the prompt for one email.

        Subject and sender are bounded here; the body is bounded upstream by
        :func:`cleaner.clean`.

        Security note: email content is interpolated directly into the prompt
        and is not sanitised. A malicious email could attempt prompt
        injection. This is a known, accepted risk for a local personal-use
        tool. The impact is limited to this email's verdict because
        _parse_response() validates the category against a fixed set and
        coerces the summary to a single line of text.
        """
        sender = str(email.get("sender", ""))[:_MAX_SENDER_CHARS]
        subject = str(email.get("subject", ""))[:_MAX_SUBJECT_CHARS]
        return "\n".join(
            [
                _SYSTEM_PROMPT,
                "Here is the email to triage:\n",
                f"From: {sender}",
                f"Subject: {subject}",
                f"Body: {email.get('body', '')}",
            ]
        )

    def _call_ollama(self, prompt: str) -> str:
        """POST the prompt to the Ollama API and return the raw response text."""
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            # Ollama defaults to a small context window and silently discards
            # whatever does not fit, so the size must be set explicitly.
            "options": {"num_ctx": self.settings.ollama_num_ctx},
        }
        logger.debug("Ollama prompt:\n%s", prompt)
        response = requests.post(
            self.generate_url, json=payload, timeout=self.settings.ollama_timeout
        )
        response.raise_for_status()
        raw = response.json()["response"]
        logger.debug("Ollama raw reply:\n%s", raw)
        return raw

    @staticmethod
    def _parse_response(raw: str, *, fallback_summary: str = "") -> tuple[Category, str]:
        """Validate the model's reply and return ``(category, summary)``.

        LLM output is untrusted in shape, not just content: the category must
        be one of :data:`CATEGORIES`, and the summary is coerced to a single
        line of text (falling back to *fallback_summary* if blank).

        Raises:
            ClassificationError: If *raw* is not a JSON object with a valid
                category.
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ClassificationError(f"reply is not valid JSON: {exc}; raw: {raw!r}") from exc

        if not isinstance(data, dict):
            raise ClassificationError(f"reply is not a JSON object; raw: {raw!r}")

        category = str(data.get("category", "")).strip().lower()
        if category not in CATEGORIES:
            raise ClassificationError(f"unknown category {data.get('category')!r}")

        summary = re.sub(r"\s+", " ", str(data.get("summary") or "")).strip()
        return cast(Category, category), summary or " ".join(str(fallback_summary).split())
