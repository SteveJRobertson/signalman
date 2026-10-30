"""Tests for the Signal notifier module.

All HTTP calls are mocked via requests-mock so no running Signal API
container is required.
"""

from __future__ import annotations

import requests
import pytest

from config import Settings
from notifier_signal import SignalNotifier
from processor_ai import TriagedItem, TriageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**overrides) -> Settings:
    """Build a Settings instance with sane test defaults."""
    base = {
        "signal_sender": "+10000000001",
        "signal_recipient": "+10000000002",
    }
    base.update(overrides)
    return Settings(**base)


def _item(category: str, summary: str, email_id: str = "id") -> TriagedItem:
    return TriagedItem(
        email_id=email_id, sender="sender@example.com", subject="subj", category=category, summary=summary
    )


def _result(*items: TriagedItem, failed: tuple[str, ...] = ()) -> TriageResult:
    return TriageResult(items=tuple(items), failed_ids=failed)


def _sample_triage() -> TriageResult:
    return _result(
        _item("urgent", "Interview invite from techcorp.com – confirm availability today"),
        _item("tasks", "Return school trip permission slip by Friday"),
        _item("digest", "Weekly newsletter: top stories summary"),
    )


def _empty_triage() -> TriageResult:
    return TriageResult()


# ---------------------------------------------------------------------------
# Tests: SignalNotifier initialisation
# ---------------------------------------------------------------------------

class TestSignalNotifierInit:
    def test_stores_sender(self):
        notifier = SignalNotifier(_settings())
        assert notifier.sender == "+10000000001"

    def test_stores_recipient(self):
        notifier = SignalNotifier(_settings())
        assert notifier.recipient == "+10000000002"

    def test_default_api_url(self):
        notifier = SignalNotifier(_settings())
        assert notifier.api_url == "http://localhost:8080"

    def test_custom_api_url(self):
        notifier = SignalNotifier(_settings(signal_api_url="http://192.168.1.10:8080"))
        assert notifier.api_url == "http://192.168.1.10:8080"


# ---------------------------------------------------------------------------
# Tests: format_message
# ---------------------------------------------------------------------------

class TestFormatMessage:
    def test_contains_header(self):
        assert "Signalman Daily Briefing" in SignalNotifier.format_message(_sample_triage())

    def test_urgent_section_present(self):
        message = SignalNotifier.format_message(_sample_triage())
        assert "Urgent" in message
        assert "Interview invite" in message

    def test_tasks_section_present(self):
        message = SignalNotifier.format_message(_sample_triage())
        assert "Tasks" in message
        assert "school trip" in message

    def test_digest_section_present(self):
        message = SignalNotifier.format_message(_sample_triage())
        assert "Digest" in message
        assert "Weekly newsletter" in message

    def test_sections_appear_in_priority_order(self):
        message = SignalNotifier.format_message(_sample_triage())

        assert message.index("Urgent") < message.index("Tasks") < message.index("Digest")

    def test_all_clear_when_empty(self):
        assert "All clear" in SignalNotifier.format_message(_empty_triage())

    def test_urgent_section_omitted_when_empty(self):
        message = SignalNotifier.format_message(_result(_item("tasks", "Do something")))
        assert "Urgent" not in message

    def test_tasks_section_omitted_when_empty(self):
        message = SignalNotifier.format_message(_result(_item("urgent", "Fix now")))
        assert "Tasks" not in message

    def test_digest_section_omitted_when_empty(self):
        message = SignalNotifier.format_message(_result(_item("urgent", "Fix now")))
        assert "Digest" not in message

    def test_multiple_urgent_items_all_present(self):
        message = SignalNotifier.format_message(
            _result(_item("urgent", "First urgent item"), _item("urgent", "Second urgent item"))
        )
        assert "First urgent item" in message
        assert "Second urgent item" in message

    def test_items_formatted_as_bullet_points(self):
        message = SignalNotifier.format_message(_result(_item("urgent", "Critical fix")))
        assert "• Critical fix" in message


class TestIgnoreCategory:
    def test_ignored_items_never_appear(self):
        message = SignalNotifier.format_message(
            _result(_item("urgent", "Real thing"), _item("ignore", "Spam about pills"))
        )

        assert "Real thing" in message
        assert "Spam about pills" not in message

    def test_only_ignored_items_reads_as_all_clear(self):
        message = SignalNotifier.format_message(_result(_item("ignore", "junk"), _item("ignore", "more")))

        assert "All clear" in message
        assert "junk" not in message


class TestFailedClassifications:
    def test_failures_are_called_out(self):
        message = SignalNotifier.format_message(
            _result(_item("urgent", "Real thing"), failed=("a", "b", "c"))
        )

        assert "Real thing" in message
        assert "⚠️ 3 emails could not be classified" in message

    def test_singular_wording(self):
        message = SignalNotifier.format_message(_result(failed=("a",)))

        assert "⚠️ 1 email could not be classified" in message

    def test_failures_with_no_items_is_not_reported_as_all_clear(self):
        """A briefing that failed to read the inbox must not look like a quiet inbox."""
        message = SignalNotifier.format_message(_result(failed=("a", "b")))

        assert "All clear" not in message
        assert "could not be classified" in message

    def test_no_warning_when_nothing_failed(self):
        assert "could not be classified" not in SignalNotifier.format_message(_sample_triage())


# ---------------------------------------------------------------------------
# Tests: send – successful HTTP call
# ---------------------------------------------------------------------------

class TestSendSuccess:
    def test_post_called_on_send(self, requests_mock):
        """A successful send makes exactly one POST to /v2/send."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=201)

        notifier = SignalNotifier(_settings())
        notifier.send(_sample_triage())

        assert requests_mock.call_count == 2  # 1 GET reachability + 1 POST send

    def test_payload_recipients_is_list(self, requests_mock):
        """The JSON payload must contain recipients as a list."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=201)

        notifier = SignalNotifier(_settings())
        notifier.send(_sample_triage())

        sent_json = requests_mock.last_request.json()
        assert isinstance(sent_json["recipients"], list)
        assert "+10000000002" in sent_json["recipients"]

    def test_payload_number_equals_sender(self, requests_mock):
        """The JSON payload number field must equal the sender."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=201)

        notifier = SignalNotifier(_settings())
        notifier.send(_sample_triage())

        sent_json = requests_mock.last_request.json()
        assert sent_json["number"] == "+10000000001"

    def test_payload_message_contains_triage_content(self, requests_mock):
        """The message in the payload contains content from the triage result."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=201)

        notifier = SignalNotifier(_settings())
        notifier.send(_sample_triage())

        sent_json = requests_mock.last_request.json()
        assert "Interview invite" in sent_json["message"]
        assert "school trip" in sent_json["message"]
        assert "Weekly newsletter" in sent_json["message"]

    def test_custom_api_url_used(self, requests_mock):
        """A custom api_url is used for both the reachability check and POST."""
        requests_mock.get("http://192.168.1.10:8080/v1/about", status_code=200)
        requests_mock.post("http://192.168.1.10:8080/v2/send", status_code=201)

        notifier = SignalNotifier(_settings(signal_api_url="http://192.168.1.10:8080"))
        notifier.send(_sample_triage())

        assert requests_mock.call_count == 2


# ---------------------------------------------------------------------------
# Tests: send – error handling
# ---------------------------------------------------------------------------

class TestSendFailure:
    def test_500_response_raises_http_error(self, requests_mock):
        """A 500 response from /v2/send raises requests.HTTPError."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=500)

        notifier = SignalNotifier(_settings())
        with pytest.raises(requests.HTTPError):
            notifier.send(_sample_triage())

    def test_404_response_raises_http_error(self, requests_mock):
        """A 404 response from /v2/send raises requests.HTTPError."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=404)

        notifier = SignalNotifier(_settings())
        with pytest.raises(requests.HTTPError):
            notifier.send(_sample_triage())

    def test_container_unreachable_raises_connection_error(self, requests_mock):
        """If the Docker container is unreachable, ConnectionError is raised."""
        requests_mock.get(
            "http://localhost:8080/v1/about",
            exc=requests.exceptions.ConnectionError,
        )

        notifier = SignalNotifier(_settings())
        with pytest.raises(ConnectionError):
            notifier.send(_sample_triage())

    def test_container_timeout_raises_connection_error(self, requests_mock):
        """If the reachability check times out, ConnectionError is raised."""
        requests_mock.get(
            "http://localhost:8080/v1/about",
            exc=requests.exceptions.Timeout,
        )

        notifier = SignalNotifier(_settings())
        with pytest.raises(ConnectionError):
            notifier.send(_sample_triage())

    def test_unhealthy_container_raises_connection_error(self, requests_mock):
        """A non-2xx response from /v1/about is treated as an unreachable container."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=503)

        notifier = SignalNotifier(_settings())
        with pytest.raises(ConnectionError):
            notifier.send(_sample_triage())


# ---------------------------------------------------------------------------
# Tests: dry-run mode
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_makes_no_http_requests(self, requests_mock):
        notifier = SignalNotifier(_settings(dry_run=True))

        notifier.send(_sample_triage())

        assert requests_mock.call_count == 0

    def test_dry_run_prints_briefing(self, capsys):
        notifier = SignalNotifier(_settings(dry_run=True))

        notifier.send(_sample_triage())

        out = capsys.readouterr().out
        assert "Signalman Daily Briefing" in out
        assert "Interview invite" in out

    def test_dry_run_does_not_need_container_up(self, requests_mock):
        """Previewing must work with the Signal container stopped."""
        requests_mock.get("http://localhost:8080/v1/about", exc=requests.exceptions.ConnectionError)
        SignalNotifier(_settings(dry_run=True)).send(_empty_triage())
