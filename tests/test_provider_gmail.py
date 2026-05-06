"""Tests for the Gmail provider module.

All Gmail API calls are mocked so no real credentials are required.
"""

import base64
import unittest
from unittest.mock import MagicMock, patch

import pytest

from provider_gmail import GmailProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(msg_id: str, subject: str, sender: str, body: str) -> dict:
    """Build a minimal Gmail API message resource."""
    raw_body = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "mimeType": "text/plain",
            "body": {"data": raw_body},
        },
    }


def _make_multipart_message(msg_id: str, subject: str, sender: str, body: str) -> dict:
    """Build a multipart Gmail API message resource (text/plain part inside parts)."""
    raw_body = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": raw_body},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(b"<b>ignored</b>").decode()},
                },
            ],
        },
    }


def _build_mock_service(list_response: dict, messages: list[dict]) -> MagicMock:
    """Wire up a mock Gmail service that returns the given data."""
    mock_service = MagicMock()
    messages_resource = mock_service.users.return_value.messages.return_value

    # list()
    list_execute = MagicMock(return_value=list_response)
    messages_resource.list.return_value.execute = list_execute

    # get() – returns different messages depending on the requested id
    msg_map = {m["id"]: m for m in messages}

    def _get_side_effect(**kwargs):
        msg_id = kwargs.get("id")
        mock_get = MagicMock()
        mock_get.execute.return_value = msg_map.get(msg_id, {})
        return mock_get

    messages_resource.get.side_effect = _get_side_effect

    return mock_service


# ---------------------------------------------------------------------------
# Tests: GmailProvider.from_service (constructor helper)
# ---------------------------------------------------------------------------

class TestGmailProviderInit:
    def test_stores_service(self):
        mock_service = MagicMock()
        provider = GmailProvider.from_service(mock_service)
        assert provider.service is mock_service


# ---------------------------------------------------------------------------
# Tests: fetch_unread_emails
# ---------------------------------------------------------------------------

class TestFetchUnreadEmails:
    def test_empty_inbox_returns_empty_list(self):
        """When the inbox has no unread messages, return an empty list."""
        mock_service = _build_mock_service(list_response={}, messages=[])
        provider = GmailProvider.from_service(mock_service)

        result = provider.fetch_unread_emails()

        assert result == []

    def test_returns_single_email(self):
        """A single unread email is returned with the correct fields."""
        msg = _make_message("msg1", "Hello World", "alice@example.com", "Body text")
        mock_service = _build_mock_service(
            list_response={"messages": [{"id": "msg1"}]},
            messages=[msg],
        )
        provider = GmailProvider.from_service(mock_service)

        result = provider.fetch_unread_emails()

        assert len(result) == 1
        assert result[0]["id"] == "msg1"
        assert result[0]["subject"] == "Hello World"
        assert result[0]["sender"] == "alice@example.com"
        assert result[0]["body"] == "Body text"

    def test_returns_multiple_emails(self):
        """Multiple unread emails are all returned."""
        msgs = [
            _make_message("msg1", "Subject 1", "a@example.com", "Body 1"),
            _make_message("msg2", "Subject 2", "b@example.com", "Body 2"),
            _make_message("msg3", "Subject 3", "c@example.com", "Body 3"),
        ]
        mock_service = _build_mock_service(
            list_response={"messages": [{"id": m["id"]} for m in msgs]},
            messages=msgs,
        )
        provider = GmailProvider.from_service(mock_service)

        result = provider.fetch_unread_emails()

        assert len(result) == 3
        assert {r["id"] for r in result} == {"msg1", "msg2", "msg3"}

    def test_multipart_email_extracts_plain_text(self):
        """For multipart messages, the text/plain part is used."""
        msg = _make_multipart_message("msg1", "Multipart", "x@example.com", "Plain part text")
        mock_service = _build_mock_service(
            list_response={"messages": [{"id": "msg1"}]},
            messages=[msg],
        )
        provider = GmailProvider.from_service(mock_service)

        result = provider.fetch_unread_emails()

        assert len(result) == 1
        assert result[0]["body"] == "Plain part text"

    def test_correct_query_sent_to_api(self):
        """The API is called with a query for unread mail from the last 24 hours."""
        mock_service = _build_mock_service(list_response={}, messages=[])
        provider = GmailProvider.from_service(mock_service)

        provider.fetch_unread_emails()

        call_kwargs = mock_service.users.return_value.messages.return_value.list.call_args[1]
        assert call_kwargs["userId"] == "me"
        assert "is:unread" in call_kwargs["q"]
        assert "newer_than:1d" in call_kwargs["q"]


# ---------------------------------------------------------------------------
# Tests: malformed / edge-case emails
# ---------------------------------------------------------------------------

class TestMalformedEmails:
    def test_missing_subject_header_defaults_to_empty_string(self):
        """Emails without a Subject header are returned with an empty subject."""
        raw_body = base64.urlsafe_b64encode(b"Body").decode()
        msg = {
            "id": "msg1",
            "payload": {
                "headers": [{"name": "From", "value": "z@example.com"}],
                "mimeType": "text/plain",
                "body": {"data": raw_body},
            },
        }
        mock_service = _build_mock_service(
            list_response={"messages": [{"id": "msg1"}]},
            messages=[msg],
        )
        provider = GmailProvider.from_service(mock_service)

        result = provider.fetch_unread_emails()

        assert result[0]["subject"] == ""

    def test_missing_from_header_defaults_to_empty_string(self):
        """Emails without a From header are returned with an empty sender."""
        raw_body = base64.urlsafe_b64encode(b"Body").decode()
        msg = {
            "id": "msg1",
            "payload": {
                "headers": [{"name": "Subject", "value": "No Sender"}],
                "mimeType": "text/plain",
                "body": {"data": raw_body},
            },
        }
        mock_service = _build_mock_service(
            list_response={"messages": [{"id": "msg1"}]},
            messages=[msg],
        )
        provider = GmailProvider.from_service(mock_service)

        result = provider.fetch_unread_emails()

        assert result[0]["sender"] == ""

    def test_empty_body_data_returns_empty_string(self):
        """Emails with no body data are returned with an empty body string."""
        msg = {
            "id": "msg1",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Empty"},
                    {"name": "From", "value": "z@example.com"},
                ],
                "mimeType": "text/plain",
                "body": {},
            },
        }
        mock_service = _build_mock_service(
            list_response={"messages": [{"id": "msg1"}]},
            messages=[msg],
        )
        provider = GmailProvider.from_service(mock_service)

        result = provider.fetch_unread_emails()

        assert result[0]["body"] == ""

    def test_missing_payload_returns_empty_fields(self):
        """Emails with a completely missing payload are handled gracefully."""
        msg = {"id": "msg1"}
        mock_service = _build_mock_service(
            list_response={"messages": [{"id": "msg1"}]},
            messages=[msg],
        )
        provider = GmailProvider.from_service(mock_service)

        result = provider.fetch_unread_emails()

        assert result[0]["subject"] == ""
        assert result[0]["sender"] == ""
        assert result[0]["body"] == ""
