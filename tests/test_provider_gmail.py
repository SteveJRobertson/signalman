"""Tests for the Gmail provider module.

All Gmail API calls are mocked so no real credentials are required.
"""

from __future__ import annotations

import base64
import dataclasses
import logging
import stat
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError, TransportError
from google_auth_oauthlib.flow import WSGITimeoutError, _WSGIRequestHandler

from config import Settings
from provider_gmail import GmailAuthError, GmailProvider


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


# ---------------------------------------------------------------------------
# Tests: HTML bodies (Phase 2)
# ---------------------------------------------------------------------------

def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _part(mime: str, text: str = "") -> dict:
    return {"mimeType": mime, "body": {"data": _b64(text)} if text else {}}


def _multipart(mime: str, *parts: dict) -> dict:
    return {"mimeType": mime, "body": {}, "parts": list(parts)}


# A table-based marketing email: nested layout tables, spacer rows and
# &nbsp; padding that, naively converted, produce screens of blank lines.
_NEWSLETTER_HTML = """
<html><head><style>td { padding: 0 }</style></head><body>
<table width="600"><tr><td>&nbsp;</td></tr><tr><td>&nbsp;</td></tr>
<tr><td><table><tr><td><img src="logo.png" alt="ACME logo"></td></tr></table></td></tr>
<tr><td>&nbsp;</td></tr><tr><td>&nbsp;</td></tr><tr><td>&nbsp;</td></tr>
<tr><td><h1>Your October statement is ready</h1></td></tr>
<tr><td>&nbsp;</td></tr><tr><td>&nbsp;</td></tr><tr><td>&nbsp;</td></tr><tr><td>&nbsp;</td></tr>
<tr><td><p>Balance due: <strong>&pound;42.00</strong> by <em>15 November</em>.</p></td></tr>
<tr><td>&nbsp;</td></tr><tr><td>&nbsp;</td></tr><tr><td>&nbsp;</td></tr>
<tr><td><a href="https://example.com/pay?token=abcdef">Pay now</a></td></tr>
</table></body></html>
"""


class TestHtmlBodies:
    def test_html_only_message_yields_non_empty_text(self):
        payload = _part("text/html", "<p>Your <b>parcel</b> has shipped.</p>")

        body = GmailProvider._extract_body(payload)

        assert "Your parcel has shipped." in body
        assert "<" not in body

    def test_plain_text_wins_when_both_parts_exist(self):
        payload = _multipart(
            "multipart/alternative",
            _part("text/plain", "plain version"),
            _part("text/html", "<p>html version</p>"),
        )

        assert GmailProvider._extract_body(payload) == "plain version"

    def test_plain_text_wins_even_when_html_comes_first(self):
        payload = _multipart(
            "multipart/alternative",
            _part("text/html", "<p>html version</p>"),
            _part("text/plain", "plain version"),
        )

        assert GmailProvider._extract_body(payload) == "plain version"

    def test_html_nested_deep_in_multipart(self):
        payload = _multipart(
            "multipart/mixed",
            _multipart(
                "multipart/related",
                _multipart("multipart/alternative", _part("text/html", "<p>deep <i>html</i></p>")),
                {"mimeType": "image/png", "body": {"attachmentId": "abc"}},
            ),
        )

        assert GmailProvider._extract_body(payload) == "deep html"

    def test_whitespace_only_plain_part_falls_through_to_html(self):
        payload = _multipart(
            "multipart/alternative",
            _part("text/plain", " \n\t \n"),
            _part("text/html", "<p>the real content</p>"),
        )

        assert GmailProvider._extract_body(payload) == "the real content"

    def test_plain_part_returned_unmodified(self):
        """Plain text is not stripped or reflowed; cleaning is a later phase."""
        payload = _part("text/plain", "  hello\n\n\n\nworld\n")

        assert GmailProvider._extract_body(payload) == "  hello\n\n\n\nworld\n"

    def test_no_text_parts_returns_empty_string(self):
        payload = _multipart("multipart/mixed", {"mimeType": "application/pdf", "body": {}})

        assert GmailProvider._extract_body(payload) == ""

    def test_empty_html_returns_empty_string(self):
        assert GmailProvider._extract_body(_part("text/html", "<div> </div>")) == ""

    def test_images_links_and_emphasis_are_dropped(self):
        payload = _part(
            "text/html",
            '<p>See <a href="https://example.com/x?t=1">the report</a> '
            '<img src="a.png" alt="chart"> <strong>now</strong></p>',
        )

        body = GmailProvider._extract_body(payload)

        assert body == "See the report now"

    def test_long_lines_are_not_hard_wrapped(self):
        sentence = "word " * 100
        body = GmailProvider._extract_body(_part("text/html", f"<p>{sentence}</p>"))

        assert "\n" not in body

    def test_newsletter_has_content_but_no_runaway_blank_lines(self):
        body = GmailProvider._extract_body(_part("text/html", _NEWSLETTER_HTML))

        assert "Your October statement is ready" in body
        assert "42.00" in body and "15 November" in body
        assert "Pay now" in body
        assert "https://" not in body and "ACME logo" not in body
        assert "\n\n\n" not in body
        assert body == body.strip()
        # no leftover markdown-table scaffolding from the layout tables
        assert not any(set(line) <= set("|-: ") for line in body.splitlines() if line)

    def test_data_table_rows_stay_readable(self):
        html = (
            "<table><tr><th>Item</th><th>Price</th></tr>"
            "<tr><td>Milk</td><td>&pound;1</td></tr></table>"
        )

        body = GmailProvider._extract_body(_part("text/html", html))

        assert "Item" in body and "Price" in body
        assert "Milk" in body and "£1" in body
        assert "Milk" not in body.split("Price")[0]  # cells not run together

    def test_html_only_message_end_to_end_through_fetch(self):
        msg = {
            "id": "html1",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Statement"},
                    {"name": "From", "value": "billing@acme.example"},
                ],
                **_part("text/html", _NEWSLETTER_HTML),
            },
        }
        service = _build_mock_service({"messages": [{"id": "html1"}]}, [msg])

        result = GmailProvider.from_service(service).fetch_unread_emails()

        assert len(result) == 1
        assert "Balance due" in result[0]["body"]


# ---------------------------------------------------------------------------
# Tests: token file permissions
# ---------------------------------------------------------------------------

def _token_settings(tmp_path) -> Settings:
    return Settings(
        signal_sender="+10000000001",
        signal_recipient="+10000000002",
        gmail_credentials_path=tmp_path / "credentials.json",
        gmail_token_path=tmp_path / "token.json",
    )


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestTokenFile:
    def test_token_from_consent_flow_is_owner_only(self, tmp_path):
        settings = _token_settings(tmp_path)
        creds = MagicMock()
        creds.to_json.return_value = '{"token": "new"}'

        with (
            patch("provider_gmail.sys.stdin") as stdin,
            patch("provider_gmail.InstalledAppFlow") as flow_cls,
            patch("provider_gmail.build"),
        ):
            stdin.isatty.return_value = True
            flow_cls.from_client_secrets_file.return_value.run_local_server.return_value = creds
            GmailProvider.from_credentials(settings)

        assert settings.gmail_token_path.read_text() == '{"token": "new"}'
        assert _mode(settings.gmail_token_path) == 0o600

    def test_refresh_tightens_a_token_file_that_was_world_readable(self, tmp_path):
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text('{"token": "a much longer old value"}')
        settings.gmail_token_path.chmod(0o644)
        creds = MagicMock(valid=False, expired=True, refresh_token="r")
        creds.to_json.return_value = '{"token": "new"}'

        with (
            patch("provider_gmail.Credentials") as creds_cls,
            patch("provider_gmail.Request"),
            patch("provider_gmail.build"),
        ):
            creds_cls.from_authorized_user_file.return_value = creds
            GmailProvider.from_credentials(settings)

        creds.refresh.assert_called_once()
        assert settings.gmail_token_path.read_text() == '{"token": "new"}'
        assert _mode(settings.gmail_token_path) == 0o600


# ---------------------------------------------------------------------------
# Tests: signing in, attended and unattended
# ---------------------------------------------------------------------------

_INVALID_GRANT = RefreshError(
    "invalid_grant: Token has been expired or revoked.",
    {"error": "invalid_grant", "raw_field": "RAW-RESPONSE-MARKER"},
)


def _expired_creds(refresh_error: Exception | None = None) -> MagicMock:
    creds = MagicMock(valid=False, expired=True, refresh_token="r")
    creds.refresh.side_effect = refresh_error
    creds.to_json.return_value = '{"token": "refreshed"}'
    return creds


class TestSignIn:
    """The launchd job has no terminal: it must fail fast, never wait for a browser."""

    def _from_credentials(self, settings, *, terminal: bool, creds=None, load_error=None):
        """Run from_credentials with Google mocked; self.flow is there to inspect afterwards."""
        new_creds = MagicMock()
        new_creds.to_json.return_value = '{"token": "from-consent"}'
        with (
            patch("provider_gmail.sys.stdin") as stdin,
            patch("provider_gmail.Credentials") as creds_cls,
            patch("provider_gmail.InstalledAppFlow") as flow_cls,
            patch("provider_gmail.Request"),
            patch("provider_gmail.build"),
        ):
            stdin.isatty.return_value = terminal
            creds_cls.from_authorized_user_file.return_value = creds
            creds_cls.from_authorized_user_file.side_effect = load_error
            flow = flow_cls.from_client_secrets_file.return_value
            flow.run_local_server.return_value = new_creds
            self.flow = flow
            self.flow_cls = flow_cls
            return GmailProvider.from_credentials(settings)

    # ---- unattended ----

    def test_missing_token_fails_fast_without_starting_the_flow(self, tmp_path):
        settings = _token_settings(tmp_path)

        with pytest.raises(GmailAuthError) as exc_info:
            self._from_credentials(settings, terminal=False)

        self.flow_cls.from_client_secrets_file.assert_not_called()
        self.flow.run_local_server.assert_not_called()
        assert str(settings.gmail_token_path) in str(exc_info.value)
        assert "python3 main.py --dry-run --limit 1" in str(exc_info.value)

    def test_rejected_refresh_token_fails_fast_and_says_why(self, tmp_path):
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text("{}")

        with pytest.raises(GmailAuthError, match="expired or revoked") as exc_info:
            self._from_credentials(settings, terminal=False, creds=_expired_creds(_INVALID_GRANT))

        self.flow.run_local_server.assert_not_called()
        assert "python3 main.py --dry-run --limit 1" in str(exc_info.value)

    def test_rejected_refresh_keeps_the_token_file(self, tmp_path):
        """Nothing better has replaced it, so nothing should overwrite it."""
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text('{"token": "old"}')

        with pytest.raises(GmailAuthError):
            self._from_credentials(settings, terminal=False, creds=_expired_creds(_INVALID_GRANT))

        assert settings.gmail_token_path.read_text() == '{"token": "old"}'

    def test_error_leaves_out_googles_raw_response(self, tmp_path):
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text("{}")

        with pytest.raises(GmailAuthError) as exc_info:
            self._from_credentials(settings, terminal=False, creds=_expired_creds(_INVALID_GRANT))

        assert "RAW-RESPONSE-MARKER" not in str(exc_info.value)

    def test_corrupt_token_file_fails_fast(self, tmp_path):
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text("not json")

        with pytest.raises(GmailAuthError, match="not a valid token file"):
            self._from_credentials(settings, terminal=False, load_error=ValueError("bad"))

        self.flow.run_local_server.assert_not_called()

    def test_expired_token_with_no_refresh_token_fails_fast(self, tmp_path):
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text("{}")
        creds = MagicMock(valid=False, expired=True, refresh_token=None)

        with pytest.raises(GmailAuthError, match="cannot be refreshed"):
            self._from_credentials(settings, terminal=False, creds=creds)

        self.flow.run_local_server.assert_not_called()

    def test_working_refresh_needs_no_terminal(self, tmp_path):
        """The normal 08:00 case: an hourly access token, renewed unattended."""
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text("{}")
        creds = _expired_creds()

        self._from_credentials(settings, terminal=False, creds=creds)

        creds.refresh.assert_called_once()
        self.flow.run_local_server.assert_not_called()
        assert settings.gmail_token_path.read_text() == '{"token": "refreshed"}'

    def test_valid_token_is_left_alone(self, tmp_path):
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text('{"token": "current"}')
        creds = MagicMock(valid=True)

        self._from_credentials(settings, terminal=False, creds=creds)

        creds.refresh.assert_not_called()
        self.flow.run_local_server.assert_not_called()
        assert settings.gmail_token_path.read_text() == '{"token": "current"}'

    def test_a_network_failure_is_not_reported_as_a_lost_sign_in(self, tmp_path):
        """Only Google refusing the token means a person has to step in."""
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text("{}")

        with pytest.raises(TransportError):
            self._from_credentials(
                settings, terminal=False, creds=_expired_creds(TransportError("no network"))
            )

    # ---- at a terminal ----

    def test_missing_token_at_a_terminal_runs_the_flow_with_a_timeout(self, tmp_path):
        settings = dataclasses.replace(
            _token_settings(tmp_path), gmail_oauth_port=8123, gmail_oauth_timeout=77
        )

        self._from_credentials(settings, terminal=True)

        self.flow.run_local_server.assert_called_once_with(
            host="127.0.0.1", port=8123, open_browser=False, timeout_seconds=77
        )
        assert settings.gmail_token_path.read_text() == '{"token": "from-consent"}'

    def test_rejected_refresh_at_a_terminal_signs_in_again(self, tmp_path):
        """No need to delete token.json by hand first."""
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text('{"token": "old"}')

        self._from_credentials(settings, terminal=True, creds=_expired_creds(_INVALID_GRANT))

        self.flow.run_local_server.assert_called_once()
        assert settings.gmail_token_path.read_text() == '{"token": "from-consent"}'

    def test_corrupt_token_at_a_terminal_signs_in_again(self, tmp_path):
        settings = _token_settings(tmp_path)
        settings.gmail_token_path.write_text("not json")

        self._from_credentials(settings, terminal=True, load_error=ValueError("bad"))

        assert settings.gmail_token_path.read_text() == '{"token": "from-consent"}'

    def test_nobody_answering_at_a_terminal_gives_up(self, tmp_path):
        settings = dataclasses.replace(_token_settings(tmp_path), gmail_oauth_timeout=5)

        with (
            patch("provider_gmail.sys.stdin") as stdin,
            patch("provider_gmail.InstalledAppFlow") as flow_cls,
        ):
            stdin.isatty.return_value = True
            flow_cls.from_client_secrets_file.return_value.run_local_server.side_effect = (
                WSGITimeoutError("Timed out")
            )
            with pytest.raises(GmailAuthError, match="after 5 seconds"):
                GmailProvider.from_credentials(settings)

        assert not settings.gmail_token_path.exists()

    # ---- revoked while the access token still looked valid ----

    def test_rejection_on_first_use_is_reported_the_same_way(self):
        service = MagicMock()
        service.users().messages().list().execute.side_effect = _INVALID_GRANT

        with pytest.raises(GmailAuthError, match="expired or revoked") as exc_info:
            GmailProvider.from_service(service).fetch_unread_emails()

        assert "python3 main.py --dry-run --limit 1" in str(exc_info.value)
        assert "RAW-RESPONSE-MARKER" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests: consent flow logging
# ---------------------------------------------------------------------------

class TestConsentLogging:
    def test_redirect_request_line_with_auth_code_is_not_logged(self, caplog):
        with caplog.at_level("INFO", logger="google_auth_oauthlib.flow"):
            _WSGIRequestHandler.log_message(
                MagicMock(), '"%s" %s %s', "GET /?state=s&code=SECRET HTTP/1.1", "200", "-"
            )

        assert "SECRET" not in caplog.text

    def test_consent_url_is_still_logged(self, caplog):
        """Piped output buffers the printed URL, so this log line is what the user sees."""
        with caplog.at_level("INFO", logger="google_auth_oauthlib.flow"):
            logging.getLogger("google_auth_oauthlib.flow").info("Please visit this URL: https://x")

        assert "Please visit this URL" in caplog.text
