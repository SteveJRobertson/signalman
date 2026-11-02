"""Gmail provider for Signalman.

Handles authentication and fetching of unread emails from the last 24 hours
via the Gmail API.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

import html2text
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from config import Settings

logger = logging.getLogger(__name__)

_QUERY = "is:unread newer_than:1d"
_PAGE_SIZE = 100  # Gmail's maximum for messages.list
_TEXT_TYPES = ("text/plain", "text/html")
_BATCH_SIZE = 50  # messages fetched per batch request

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_SIGN_IN_HINT = "Run `python3 main.py --dry-run --limit 1` in a terminal to sign in again."


class GmailAuthError(Exception):
    """Gmail needs a person: there is no usable sign-in and nobody here to redo it."""


class _DropConsentRedirect(logging.Filter):
    """Hide the consent redirect's request line, which contains the OAuth code.

    The same logger also emits the consent URL, which has to stay: the printed
    copy is held back by stdout buffering when output is piped.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.funcName != "log_message"


logging.getLogger("google_auth_oauthlib.flow").addFilter(_DropConsentRedirect())


def _save_token(token_path: Path, token_json: str) -> None:
    """Write the OAuth token so that only the owner can read it."""
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(fd, 0o600)  # os.open's mode only applies to a newly created file
        handle.write(token_json)


def _refusal_reason(exc: RefreshError) -> str:
    """Return Google's one-line reason, leaving out the raw response that follows it."""
    return str(exc.args[0]) if exc.args else "no reason given"


def _sign_in(settings: Settings, problem: str) -> Credentials:
    """Run the interactive consent flow, if there is anyone to complete it.

    ``run_local_server`` blocks until the browser redirect arrives, so the
    launchd job (whose stdin is ``/dev/null``) must never reach it: it would
    wait forever and tell no one. At a terminal it is still bounded, in case
    whoever started it has walked away.

    Raises:
        GmailAuthError: If nobody is at a terminal, or nobody answered in time.
    """
    if not sys.stdin.isatty():
        raise GmailAuthError(f"Gmail needs signing in: {problem}. {_SIGN_IN_HINT}")

    logger.warning("Gmail needs signing in: %s.", problem)
    flow = InstalledAppFlow.from_client_secrets_file(str(settings.gmail_credentials_path), SCOPES)
    try:
        return flow.run_local_server(
            host="127.0.0.1",
            port=settings.gmail_oauth_port,
            open_browser=False,
            timeout_seconds=settings.gmail_oauth_timeout,
        )
    except WSGITimeoutError as exc:
        raise GmailAuthError(
            f"Gave up waiting for Gmail sign-in after {settings.gmail_oauth_timeout} seconds."
        ) from exc


class GmailProvider:
    """Fetches unread emails from Gmail using the Google API."""

    def __init__(self, service: Any, max_emails: int | None = None) -> None:
        self.service = service
        self.max_emails = max_emails
        self.failed_ids: list[str] = []

    @classmethod
    def from_service(cls, service: Any) -> "GmailProvider":
        """Create a GmailProvider from a pre-built Gmail API service object.

        Useful for testing – callers can inject a mock service directly.
        """
        return cls(service)

    @classmethod
    def from_credentials(cls, settings: Settings) -> "GmailProvider":
        """Authenticate with OAuth2 and return a ready-to-use GmailProvider.

        Looks for ``settings.gmail_token_path`` (refreshed automatically) and
        falls back to ``settings.gmail_credentials_path`` for the interactive
        OAuth flow, but only when someone is at a terminal to complete it.

        Raises:
            GmailAuthError: If there is no usable sign-in and nobody to redo it.
        """
        token_path = settings.gmail_token_path
        creds: Credentials | None = None
        problem = f"there is no saved sign-in at {token_path}"

        if token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
                problem = "the saved sign-in has expired and cannot be refreshed"
            except ValueError:
                problem = f"{token_path} is not a valid token file"

        if not creds or not creds.valid:
            refreshed = False
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    refreshed = True
                except RefreshError as exc:
                    problem = f"Google rejected the saved sign-in ({_refusal_reason(exc)})"
            if not refreshed:
                creds = _sign_in(settings, problem)
            _save_token(token_path, creds.to_json())

        service = build("gmail", "v1", credentials=creds)
        return cls(service, max_emails=settings.gmail_max_emails)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_unread_emails(self) -> list[dict]:
        """Return a list of unread emails received in the last 24 hours.

        Each item in the returned list is a dict with keys:
            - ``id``      : Gmail message ID
            - ``subject`` : Email subject (empty string if missing)
            - ``sender``  : From header value (empty string if missing)
            - ``body``    : Body as text — plain if available, else converted HTML (empty string if missing)

        Raises:
            GmailAuthError: If Google rejects the sign-in. A token revoked
                before its expiry date only shows up here, on first use.
        """
        try:
            message_ids = self._list_unread_ids()
        except RefreshError as exc:
            raise GmailAuthError(
                f"Google rejected the saved sign-in ({_refusal_reason(exc)}). {_SIGN_IN_HINT}"
            ) from exc
        emails_by_id: dict[str, dict] = {}
        self.failed_ids = []

        for start in range(0, len(message_ids), _BATCH_SIZE):
            chunk = message_ids[start : start + _BATCH_SIZE]
            emails_by_id.update(self._fetch_chunk(chunk))

        if self.failed_ids:
            logger.warning(
                "Could not fetch %d of %d email(s): %s",
                len(self.failed_ids),
                len(message_ids),
                ", ".join(self.failed_ids),
            )

        # Batch callbacks fire in arbitrary order; restore Gmail's ordering.
        return [emails_by_id[i] for i in message_ids if i in emails_by_id]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _list_unread_ids(self) -> list[str]:
        """Return the ids of unread messages, following ``nextPageToken``.

        Stops at ``max_emails`` (if set) and logs a warning when that cap
        hides mail, so a truncated inbox is never silent.
        """
        ids: list[str] = []
        page_token: str | None = None
        while True:
            request_kwargs: dict[str, Any] = {
                "userId": "me",
                "q": _QUERY,
                "maxResults": _PAGE_SIZE,
            }
            if page_token:
                request_kwargs["pageToken"] = page_token
            response = self.service.users().messages().list(**request_kwargs).execute()
            ids.extend(ref["id"] for ref in response.get("messages", []))
            page_token = response.get("nextPageToken")

            capped = self.max_emails is not None and len(ids) >= self.max_emails
            if not page_token or capped:
                break

        if self.max_emails is not None and (
            len(ids) > self.max_emails or (page_token and len(ids) >= self.max_emails)
        ):
            logger.warning(
                "More unread email than the limit of %d; only the first %d will be processed.",
                self.max_emails,
                self.max_emails,
            )
            ids = ids[: self.max_emails]
        return ids

    def _fetch_chunk(self, message_ids: list[str]) -> dict[str, dict]:
        """Fetch *message_ids* in one batch request, keyed by message id.

        A message that fails is recorded in ``self.failed_ids`` and skipped;
        it never aborts the rest of the batch.
        """
        results: dict[str, dict] = {}

        def _on_response(request_id: str, response: dict, exception: Exception | None) -> None:
            if exception is not None:
                self.failed_ids.append(request_id)
                return
            try:
                results[request_id] = self._parse_message(response)
            except Exception:  # noqa: BLE001 - one malformed message must not abort the run
                self.failed_ids.append(request_id)

        batch = self.service.new_batch_http_request()
        for message_id in message_ids:
            batch.add(
                self.service.users().messages().get(userId="me", id=message_id, format="full"),
                callback=_on_response,
                request_id=message_id,
            )
        batch.execute()
        return results

    @staticmethod
    def _parse_message(msg: dict) -> dict:
        """Extract the relevant fields from a raw Gmail API message dict."""
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        subject = next(
            (h["value"] for h in headers if h["name"].lower() == "subject"), ""
        )
        sender = next(
            (h["value"] for h in headers if h["name"].lower() == "from"), ""
        )
        body = GmailProvider._extract_body(payload)

        return {
            "id": msg.get("id", ""),
            "subject": subject,
            "sender": sender,
            "body": body,
        }

    @staticmethod
    def _extract_body(payload: dict) -> str:
        """Return the message body as text suitable for an LLM.

        Walks the payload tree (simple, multipart, or arbitrarily nested
        multipart) collecting the first non-blank ``text/plain`` and the
        first non-blank ``text/html`` part. Plain text wins; HTML (most
        newsletters, receipts and marketing mail) is converted to text only
        when there is no usable plain part. Returns ``""`` if neither exists.
        """
        found: dict[str, str] = {}
        GmailProvider._collect_text_parts(payload, found)
        if "text/plain" in found:
            return found["text/plain"]
        if "text/html" in found:
            return _html_to_text(found["text/html"])
        return ""

    @staticmethod
    def _collect_text_parts(payload: dict, found: dict[str, str]) -> None:
        """Record the first non-blank decoded part of each text type in *found*."""
        mime_type = payload.get("mimeType", "")
        if mime_type in _TEXT_TYPES and mime_type not in found:
            data = payload.get("body", {}).get("data", "")
            text = _decode_base64(data) if data else ""
            if text.strip():
                found[mime_type] = text

        for part in payload.get("parts", []):
            GmailProvider._collect_text_parts(part, found)


def _decode_base64(data: str) -> str:
    """Decode a URL-safe base64-encoded string returned by the Gmail API."""
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _html_to_text(html: str) -> str:
    """Convert HTML to compact plain text for LLM consumption.

    Images and links are dropped (alt text and URLs are noise that burns
    tokens), lines are not hard-wrapped, table scaffolding lines are removed
    and the runs of blank lines that table-based newsletter markup produces
    are collapsed.
    """
    converter = html2text.HTML2Text()
    converter.ignore_images = True
    converter.ignore_links = True
    converter.ignore_emphasis = True
    converter.body_width = 0
    text = converter.handle(html)
    text = re.sub(r"[ \t]+\n", "\n", text)
    # Drop lines that are only table/rule scaffolding (e.g. "---", "|",
    # "---|---"). Layout tables emit these by the dozen; real data tables
    # keep their "a | b" rows.
    text = re.sub(r"^[ \t|:-]+$", "", text, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
