"""Tests for the Gmail provider module.

All Gmail API calls are mocked so no real credentials are required.
"""

from __future__ import annotations

import base64
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


def _make_nested_multipart_message(msg_id: str, subject: str, sender: str, body: str) -> dict:
    """Build a multipart/mixed > multipart/alternative > text/plain message."""
    raw_body = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "mimeType": "multipart/mixed",
            "body": {},
            "parts": [
                {
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
            ],
        },
    }


class FakeBatch:
    """Stand-in for ``service.new_batch_http_request()``.

    ``execute()`` runs each queued request and fires its callback, in
    reverse order by default (real Gmail batches reply in arbitrary order).
    Ids in ``failing_ids`` get an exception passed to their callback instead.
    """

    def __init__(self, failing_ids: frozenset[str] = frozenset(), reverse: bool = True) -> None:
        self.failing_ids = failing_ids
        self.reverse = reverse
        self.queued: list[tuple[MagicMock, object, str]] = []

    def add(self, request, callback, request_id):
        self.queued.append((request, callback, request_id))

    def execute(self):
        queued = list(reversed(self.queued)) if self.reverse else self.queued
        for request, callback, request_id in queued:
            if request_id in self.failing_ids:
                callback(request_id, None, RuntimeError("boom"))
            else:
                callback(request_id, request.execute(), None)


def _build_mock_service(
    list_response: dict | list[dict],
    messages: list[dict],
    failing_ids: frozenset[str] = frozenset(),
) -> MagicMock:
    """Wire up a mock Gmail service that returns the given data.

    *list_response* may be a list of page dicts, returned one per ``list()``
    call, to exercise pagination.
    """
    mock_service = MagicMock()
    messages_resource = mock_service.users.return_value.messages.return_value

    # list()
    pages = list_response if isinstance(list_response, list) else [list_response]
    messages_resource.list.return_value.execute.side_effect = pages
    mock_service.new_batch_http_request.side_effect = lambda: FakeBatch(failing_ids)

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

    def test_deeply_nested_multipart_extracts_plain_text(self):
        """multipart/mixed > multipart/alternative > text/plain is handled recursively."""
        msg = _make_nested_multipart_message("msg1", "Nested", "x@example.com", "Nested plain text")
        mock_service = _build_mock_service(
            list_response={"messages": [{"id": "msg1"}]},
            messages=[msg],
        )
        provider = GmailProvider.from_service(mock_service)

        result = provider.fetch_unread_emails()

        assert len(result) == 1
        assert result[0]["body"] == "Nested plain text"

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


# ---------------------------------------------------------------------------
# Tests: max_emails cap (used by --limit)
# ---------------------------------------------------------------------------

class TestMaxEmailsCap:
    def _service(self, n: int) -> MagicMock:
        msgs = [_make_message(f"m{i}", f"S{i}", "a@b.com", "body") for i in range(n)]
        return _build_mock_service({"messages": [{"id": m["id"]} for m in msgs]}, msgs)

    def test_cap_limits_messages_fetched(self):
        service = self._service(5)
        provider = GmailProvider(service, max_emails=2)

        result = provider.fetch_unread_emails()

        assert [e["id"] for e in result] == ["m0", "m1"]
        assert service.users.return_value.messages.return_value.get.call_count == 2

    def test_cap_logs_warning_when_truncating(self, caplog):
        provider = GmailProvider(self._service(5), max_emails=2)

        with caplog.at_level("WARNING"):
            provider.fetch_unread_emails()

        assert "limit of 2" in caplog.text

    def test_no_warning_when_under_cap(self, caplog):
        provider = GmailProvider(self._service(2), max_emails=5)

        with caplog.at_level("WARNING"):
            result = provider.fetch_unread_emails()

        assert len(result) == 2
        assert caplog.text == ""

    def test_no_warning_when_exactly_at_cap_with_no_more_pages(self, caplog):
        provider = GmailProvider(self._service(3), max_emails=3)

        with caplog.at_level("WARNING"):
            result = provider.fetch_unread_emails()

        assert len(result) == 3
        assert caplog.text == ""

    def test_cap_stops_paging_and_warns_when_more_pages_exist(self, caplog):
        msgs = [_make_message(f"m{i}", f"S{i}", "a@b.com", "body") for i in range(6)]
        pages = [
            {"messages": [{"id": "m0"}, {"id": "m1"}, {"id": "m2"}], "nextPageToken": "p2"},
            {"messages": [{"id": "m3"}, {"id": "m4"}, {"id": "m5"}]},
        ]
        service = _build_mock_service(pages, msgs)
        provider = GmailProvider(service, max_emails=3)

        with caplog.at_level("WARNING"):
            result = provider.fetch_unread_emails()

        assert [e["id"] for e in result] == ["m0", "m1", "m2"]
        assert service.users.return_value.messages.return_value.list.call_count == 1
        assert "limit of 3" in caplog.text


# ---------------------------------------------------------------------------
# Tests: pagination
# ---------------------------------------------------------------------------

class TestPagination:
    @staticmethod
    def _pages(*sizes: int):
        """Build page dicts of the given sizes plus the matching messages."""
        msgs, pages, n = [], [], 0
        for i, size in enumerate(sizes):
            refs = []
            for _ in range(size):
                msgs.append(_make_message(f"m{n}", f"S{n}", "a@b.com", f"body {n}"))
                refs.append({"id": f"m{n}"})
                n += 1
            page = {"messages": refs}
            if i < len(sizes) - 1:
                page["nextPageToken"] = f"token{i + 1}"
            pages.append(page)
        return pages, msgs

    def test_two_pages_are_combined_in_order(self):
        pages, msgs = self._pages(3, 2)
        provider = GmailProvider.from_service(_build_mock_service(pages, msgs))

        result = provider.fetch_unread_emails()

        assert [e["id"] for e in result] == ["m0", "m1", "m2", "m3", "m4"]

    def test_three_pages_are_combined_in_order(self):
        pages, msgs = self._pages(2, 2, 1)
        provider = GmailProvider.from_service(_build_mock_service(pages, msgs))

        result = provider.fetch_unread_emails()

        assert [e["id"] for e in result] == ["m0", "m1", "m2", "m3", "m4"]

    def test_page_token_is_passed_to_next_request(self):
        pages, msgs = self._pages(1, 1, 1)
        service = _build_mock_service(pages, msgs)

        GmailProvider.from_service(service).fetch_unread_emails()

        calls = service.users.return_value.messages.return_value.list.call_args_list
        assert [c.kwargs.get("pageToken") for c in calls] == [None, "token1", "token2"]
        assert all(c.kwargs["maxResults"] == 100 for c in calls)

    def test_empty_page_in_the_middle_does_not_stop_paging(self):
        msgs = [_make_message("m0", "S", "a@b.com", "x"), _make_message("m1", "S", "a@b.com", "y")]
        pages = [
            {"messages": [{"id": "m0"}], "nextPageToken": "t1"},
            {"nextPageToken": "t2"},
            {"messages": [{"id": "m1"}]},
        ]
        provider = GmailProvider.from_service(_build_mock_service(pages, msgs))

        assert [e["id"] for e in provider.fetch_unread_emails()] == ["m0", "m1"]


# ---------------------------------------------------------------------------
# Tests: batched fetch
# ---------------------------------------------------------------------------

class TestBatchedFetch:
    @staticmethod
    def _service(n: int, failing_ids: frozenset[str] = frozenset()) -> MagicMock:
        msgs = [_make_message(f"m{i}", f"S{i}", "a@b.com", f"body {i}") for i in range(n)]
        return _build_mock_service(
            {"messages": [{"id": m["id"]} for m in msgs]}, msgs, failing_ids=failing_ids
        )

    def test_output_order_matches_listing_regardless_of_callback_order(self):
        """FakeBatch fires callbacks in reverse; the result must still be in listing order."""
        provider = GmailProvider.from_service(self._service(10))

        result = provider.fetch_unread_emails()

        assert [e["id"] for e in result] == [f"m{i}" for i in range(10)]
        assert result[3]["body"] == "body 3"

    def test_messages_fetched_in_chunks_of_50(self):
        service = self._service(120)

        result = GmailProvider.from_service(service).fetch_unread_emails()

        assert len(result) == 120
        assert service.new_batch_http_request.call_count == 3  # 50 + 50 + 20

    def test_one_failed_message_does_not_lose_the_other_49(self):
        service = self._service(50, failing_ids=frozenset({"m17"}))
        provider = GmailProvider.from_service(service)

        result = provider.fetch_unread_emails()

        assert len(result) == 49
        assert "m17" not in [e["id"] for e in result]
        assert provider.failed_ids == ["m17"]

    def test_failures_are_logged(self, caplog):
        provider = GmailProvider.from_service(self._service(5, failing_ids=frozenset({"m1", "m3"})))

        with caplog.at_level("WARNING"):
            provider.fetch_unread_emails()

        assert "Could not fetch 2 of 5" in caplog.text
        assert "m1" in caplog.text and "m3" in caplog.text

    def test_failed_ids_reset_on_each_fetch(self):
        provider = GmailProvider.from_service(self._service(3, failing_ids=frozenset({"m0"})))
        provider.fetch_unread_emails()
        assert provider.failed_ids == ["m0"]

        provider.service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [{}]
        provider.fetch_unread_emails()

        assert provider.failed_ids == []

    def test_malformed_response_is_isolated(self):
        """A response that makes parsing blow up is recorded as failed, not raised."""
        service = self._service(3)
        original = GmailProvider._parse_message

        def flaky(msg):
            if msg.get("id") == "m1":
                raise KeyError("bad header")
            return original(msg)

        provider = GmailProvider.from_service(service)
        with patch.object(GmailProvider, "_parse_message", staticmethod(flaky)):
            result = provider.fetch_unread_emails()

        assert [e["id"] for e in result] == ["m0", "m2"]
        assert provider.failed_ids == ["m1"]
