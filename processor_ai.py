"""AI processor for Signalman.

Classifies each email with a local Ollama LLM — one call per email — and
returns a :class:`TriageResult` mapping every verdict back to the email that
produced it. Each email lands in exactly one category:

- ``urgent``: needs a reply or action today.
- ``tasks``:  contains an action item, but not time-critical today.
- ``digest``: low-priority information worth a skim.
- ``ignore``: junk or automated noise; filtered out before the briefing.

Replies are constrained by a JSON schema where Ollama supports one, then
validated anyway. Retryable failures are retried with backoff; an email that
still has no verdict is recorded as failed and the run continues, unless Ollama
itself keeps failing, which aborts the run so it can be reported.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
import time
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

import requests

from config import Settings

logger = logging.getLogger(__name__)

Category = Literal["urgent", "tasks", "digest", "ignore"]
CATEGORIES: tuple[str, ...] = get_args(Category)

_MAX_SUBJECT_CHARS = 200
_MAX_SENDER_CHARS = 100

# Passed as Ollama's ``format``: constrains the reply structurally, not politely.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "summary": {"type": "string"},
    },
    "required": ["category", "summary"],
}
_MIN_SCHEMA_VERSION = (0, 5, 0)  # the first Ollama to accept a schema in ``format``
_VERSION_PROBE_TIMEOUT = 5  # seconds

_RETRY_BACKOFF_SECONDS = 1  # doubles after each failed attempt
# At temperature 0 the model replays the same reply exactly, so retrying an
# unusable one needs a little randomness to have any chance of a different answer.
_RETRY_TEMPERATURE = 0.3
_TRANSIENT_REQUEST_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
# One email can time out on its own; this many in a row means Ollama is the problem.
_MAX_REQUEST_FAILURES_IN_A_ROW = 3


class ClassificationError(Exception):
    """No usable verdict was obtained for one email.

    ``retryable`` says whether another attempt could plausibly help.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class OllamaRequestError(ClassificationError):
    """Ollama itself failed (no reply at all), as opposed to giving an unusable one."""


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
        self._format: dict[str, Any] | str | None = None  # probed on first use

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def triage(self, emails: list[dict]) -> TriageResult:
        """Classify each of *emails* and return the combined result.

        Emails are processed sequentially. An email with no usable verdict
        after retries is recorded in ``TriageResult.failed_ids`` and the run
        continues. A dead or hung Ollama is the exception: rather than spend
        hours recording every email as failed, the run aborts once
        ``_MAX_REQUEST_FAILURES_IN_A_ROW`` emails in a row get no reply at all.

        Args:
            emails: Email dicts with keys ``id``, ``subject``, ``sender`` and
                ``body`` (as returned by :class:`~provider_gmail.GmailProvider`).

        Raises:
            OllamaRequestError: If Ollama keeps failing, email after email.
        """
        items: list[TriagedItem] = []
        failed: list[str] = []
        request_failures = 0

        for position, email in enumerate(emails, start=1):
            logger.info("Classifying %d/%d…", position, len(emails))
            try:
                items.append(self._classify_one(email))
                request_failures = 0
            except ClassificationError as exc:
                logger.warning("Could not classify email %r: %s", email.get("id", ""), exc)
                failed.append(email.get("id", ""))
                request_failures = request_failures + 1 if isinstance(exc, OllamaRequestError) else 0
                if request_failures >= _MAX_REQUEST_FAILURES_IN_A_ROW:
                    raise OllamaRequestError(
                        f"Ollama failed on {request_failures} emails in a row; last error: {exc}",
                        retryable=False,
                    ) from exc

        return TriageResult(items=tuple(items), failed_ids=tuple(failed))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _classify_one(self, email: dict) -> TriagedItem:
        """Ask the model for a verdict on a single email.

        Raises:
            ClassificationError: If no usable verdict was obtained.
        """
        category, summary = self._verdict_with_retries(
            self._build_prompt(email), fallback_summary=email.get("subject", "")
        )
        return TriagedItem(
            email_id=email.get("id", ""),
            sender=email.get("sender", ""),
            subject=email.get("subject", ""),
            category=category,
            summary=summary,
        )

    def _verdict_with_retries(self, prompt: str, *, fallback_summary: str) -> tuple[Category, str]:
        """Call the model and parse its reply, retrying whatever is retryable.

        ``settings.ollama_retries`` extra attempts follow the first, 1s then 2s
        apart. Timeouts, connection errors, 5xx and unusable replies are
        retried; a 4xx would fail identically, so it never is.

        Raises:
            ClassificationError: On a non-retryable failure, or once the
                attempts run out.
        """
        attempts = self.settings.ollama_retries + 1
        temperature = 0.0
        for attempt in range(1, attempts + 1):
            try:
                raw = self._call_ollama(prompt, temperature=temperature)
                return self._parse_response(raw, fallback_summary=fallback_summary)
            except ClassificationError as exc:
                if not exc.retryable or attempt == attempts:
                    raise
                delay = _RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1)
                logger.warning(
                    "Attempt %d/%d failed (%s); retrying in %ds…", attempt, attempts, exc, delay
                )
                time.sleep(delay)
                if not isinstance(exc, OllamaRequestError):
                    temperature = _RETRY_TEMPERATURE
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _response_format(self) -> dict[str, Any] | str:
        """Return Ollama's ``format`` value, probing the server's version once.

        A server that rejects a schema would fail every email, so anything not
        confirmed as new enough gets plain ``"json"``; ``_parse_response()``
        checks the keys either way.
        """
        if self._format is None:
            supports_schema = self._ollama_version() >= _MIN_SCHEMA_VERSION
            self._format = _RESPONSE_SCHEMA if supports_schema else "json"
            if not supports_schema:
                logger.warning("Ollama older than 0.5.0 or version unknown; using plain JSON mode.")
        return self._format

    def _ollama_version(self) -> tuple[int, ...]:
        """Return the server's version as numbers (compared numerically, since
        ``"0.40.5" < "0.5.0"`` as text), or ``()`` if it cannot be determined."""
        try:
            response = requests.get(
                f"{self.settings.ollama_url}/api/version", timeout=_VERSION_PROBE_TIMEOUT
            )
            response.raise_for_status()
            return tuple(int(part) for part in re.findall(r"\d+", response.json()["version"])[:3])
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logger.warning("Could not read the Ollama version: %s", exc)
            return ()

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

    def _call_ollama(self, prompt: str, *, temperature: float = 0.0) -> str:
        """POST the prompt to the Ollama API and return the raw response text.

        Raises:
            OllamaRequestError: If no reply came back.
            ClassificationError: If the response body is not the expected shape.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": self._response_format(),
            # Ollama defaults to a small context window and silently discards
            # whatever does not fit, so the size must be set explicitly.
            # Temperature 0 so that the same inbox gives the same briefing.
            "options": {"num_ctx": self.settings.ollama_num_ctx, "temperature": temperature},
        }
        logger.debug("Ollama prompt:\n%s", prompt)
        try:
            response = requests.post(
                self.generate_url, json=payload, timeout=self.settings.ollama_timeout
            )
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise OllamaRequestError(str(exc), retryable=exc.response.status_code >= 500) from exc
        except requests.RequestException as exc:
            raise OllamaRequestError(
                str(exc), retryable=isinstance(exc, _TRANSIENT_REQUEST_ERRORS)
            ) from exc

        try:
            raw = response.json()["response"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ClassificationError(f"unexpected response shape from Ollama: {exc!r}") from exc
        logger.debug("Ollama raw reply:\n%s", raw)
        return raw

    @staticmethod
    def _parse_response(raw: str, *, fallback_summary: str = "") -> tuple[Category, str]:
        """Validate the model's reply and return ``(category, summary)``.

        LLM output is untrusted in shape, not just content: a schema makes a
        malformed reply unlikely, not impossible, and older servers have no
        schema at all. Both keys must be present, the category must be one of
        :data:`CATEGORIES`, and the summary is coerced to a single line of text
        (falling back to *fallback_summary* if blank).

        Error messages leave out the reply itself, which echoes email content
        and would otherwise reach the on-disk log; ``--verbose`` shows it.

        Raises:
            ClassificationError: If *raw* is not a JSON object with both keys
                and a valid category.
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ClassificationError(f"reply is not valid JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise ClassificationError("reply is not a JSON object")

        category = str(data.get("category", "")).strip().lower()
        if category not in CATEGORIES:
            raise ClassificationError(f"unknown category {data.get('category')!r}")

        if "summary" not in data:
            raise ClassificationError("reply has no summary key")

        summary = re.sub(r"\s+", " ", str(data.get("summary") or "")).strip()
        return cast(Category, category), summary or " ".join(str(fallback_summary).split())
