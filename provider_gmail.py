"""Gmail provider for Signalman.

Handles authentication and fetching of unread emails from the last 24 hours
via the Gmail API.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")
CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")


class GmailProvider:
    """Fetches unread emails from Gmail using the Google API."""

    def __init__(self, service: Any) -> None:
        self.service = service

    @classmethod
    def from_service(cls, service: Any) -> "GmailProvider":
        """Create a GmailProvider from a pre-built Gmail API service object.

        Useful for testing – callers can inject a mock service directly.
        """
        return cls(service)

    @classmethod
    def from_credentials(cls) -> "GmailProvider":
        """Authenticate with OAuth2 and return a ready-to-use GmailProvider.

        Looks for ``token.json`` (refreshed automatically) and falls back to
        ``credentials.json`` for the initial interactive OAuth flow.
        """
        creds: Credentials | None = None

        if os.path.exists(TOKEN_PATH):
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(TOKEN_PATH, "w") as token_file:
                token_file.write(creds.to_json())

        service = build("gmail", "v1", credentials=creds)
        return cls(service)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_unread_emails(self) -> list[dict]:
        """Return a list of unread emails received in the last 24 hours.

        Each item in the returned list is a dict with keys:
            - ``id``      : Gmail message ID
            - ``subject`` : Email subject (empty string if missing)
            - ``sender``  : From header value (empty string if missing)
            - ``body``    : Plain-text body (empty string if missing)
        """
        results = (
            self.service.users()
            .messages()
            .list(userId="me", q="is:unread newer_than:1d")
            .execute()
        )

        message_refs = results.get("messages", [])
        emails = []
        for ref in message_refs:
            msg = (
                self.service.users()
                .messages()
                .get(userId="me", id=ref["id"], format="full")
                .execute()
            )
            emails.append(self._parse_message(msg))

        return emails

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
        """Decode the plain-text body from a message payload.

        Handles simple (``text/plain``), single-level multipart, and deeply
        nested multipart messages (e.g. multipart/mixed > multipart/alternative
        > text/plain) by recursing into sub-parts.
        """
        mime_type = payload.get("mimeType", "")

        if mime_type == "text/plain":
            data = payload.get("body", {}).get("data", "")
            return _decode_base64(data) if data else ""

        # Multipart: recurse into each part, return the first text/plain found.
        for part in payload.get("parts", []):
            result = GmailProvider._extract_body(part)
            if result:
                return result

        return ""


def _decode_base64(data: str) -> str:
    """Decode a URL-safe base64-encoded string returned by the Gmail API."""
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
