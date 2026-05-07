"""Tests for the Signal notifier module.

All HTTP calls are mocked via requests-mock so no running Signal API
container is required.
"""

from __future__ import annotations

import requests
import pytest

from notifier_signal import SignalNotifier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_triage() -> dict:
    return {
        "urgent": ["Interview invite from techcorp.com – confirm availability today"],
        "tasks": ["Return school trip permission slip by Friday"],
        "digest": ["Weekly newsletter: top stories summary"],
    }


def _empty_triage() -> dict:
    return {"urgent": [], "tasks": [], "digest": []}


# ---------------------------------------------------------------------------
# Tests: SignalNotifier initialisation
# ---------------------------------------------------------------------------

class TestSignalNotifierInit:
    def test_stores_sender(self):
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        assert notifier.sender == "+10000000001"

    def test_stores_recipient(self):
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        assert notifier.recipient == "+10000000002"

    def test_default_api_url(self):
        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        assert notifier.api_url == "http://localhost:8080"

    def test_custom_api_url(self):
        notifier = SignalNotifier(
            sender="+10000000001",
            recipient="+10000000002",
            api_url="http://192.168.1.10:8080",
        )
        assert notifier.api_url == "http://192.168.1.10:8080"


# ---------------------------------------------------------------------------
# Tests: format_message
# ---------------------------------------------------------------------------

class TestFormatMessage:
    def test_contains_header(self):
        message = SignalNotifier.format_message(_sample_triage())
        assert "Signalman Daily Briefing" in message

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

    def test_all_clear_when_empty(self):
        message = SignalNotifier.format_message(_empty_triage())
        assert "All clear" in message

    def test_urgent_section_omitted_when_empty(self):
        triage = {"urgent": [], "tasks": ["Do something"], "digest": []}
        message = SignalNotifier.format_message(triage)
        assert "Urgent" not in message

    def test_tasks_section_omitted_when_empty(self):
        triage = {"urgent": ["Fix now"], "tasks": [], "digest": []}
        message = SignalNotifier.format_message(triage)
        assert "Tasks" not in message

    def test_digest_section_omitted_when_empty(self):
        triage = {"urgent": [], "tasks": [], "digest": []}
        message = SignalNotifier.format_message(triage)
        assert "Digest" not in message

    def test_multiple_urgent_items_all_present(self):
        triage = {
            "urgent": ["First urgent item", "Second urgent item"],
            "tasks": [],
            "digest": [],
        }
        message = SignalNotifier.format_message(triage)
        assert "First urgent item" in message
        assert "Second urgent item" in message

    def test_items_formatted_as_bullet_points(self):
        triage = {"urgent": ["Critical fix"], "tasks": [], "digest": []}
        message = SignalNotifier.format_message(triage)
        assert "• Critical fix" in message


# ---------------------------------------------------------------------------
# Tests: send – successful HTTP call
# ---------------------------------------------------------------------------

class TestSendSuccess:
    def test_post_called_on_send(self, requests_mock):
        """A successful send makes exactly one POST to /v2/send."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=201)

        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        notifier.send(_sample_triage())

        assert requests_mock.call_count == 2  # 1 GET reachability + 1 POST send

    def test_payload_recipients_is_list(self, requests_mock):
        """The JSON payload must contain recipients as a list."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=201)

        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        notifier.send(_sample_triage())

        sent_json = requests_mock.last_request.json()
        assert isinstance(sent_json["recipients"], list)
        assert "+10000000002" in sent_json["recipients"]

    def test_payload_number_equals_sender(self, requests_mock):
        """The JSON payload number field must equal the sender."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=201)

        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        notifier.send(_sample_triage())

        sent_json = requests_mock.last_request.json()
        assert sent_json["number"] == "+10000000001"

    def test_payload_message_contains_triage_content(self, requests_mock):
        """The message in the payload contains content from the triage dict."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=201)

        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        notifier.send(_sample_triage())

        sent_json = requests_mock.last_request.json()
        assert "Interview invite" in sent_json["message"]
        assert "school trip" in sent_json["message"]
        assert "Weekly newsletter" in sent_json["message"]

    def test_custom_api_url_used(self, requests_mock):
        """A custom api_url is used for both the reachability check and POST."""
        requests_mock.get("http://192.168.1.10:8080/v1/about", status_code=200)
        requests_mock.post("http://192.168.1.10:8080/v2/send", status_code=201)

        notifier = SignalNotifier(
            sender="+10000000001",
            recipient="+10000000002",
            api_url="http://192.168.1.10:8080",
        )
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

        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        with pytest.raises(requests.HTTPError):
            notifier.send(_sample_triage())

    def test_404_response_raises_http_error(self, requests_mock):
        """A 404 response from /v2/send raises requests.HTTPError."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=200)
        requests_mock.post("http://localhost:8080/v2/send", status_code=404)

        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        with pytest.raises(requests.HTTPError):
            notifier.send(_sample_triage())

    def test_container_unreachable_raises_connection_error(self, requests_mock):
        """If the Docker container is unreachable, ConnectionError is raised."""
        requests_mock.get(
            "http://localhost:8080/v1/about",
            exc=requests.exceptions.ConnectionError,
        )

        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        with pytest.raises(ConnectionError):
            notifier.send(_sample_triage())

    def test_container_timeout_raises_connection_error(self, requests_mock):
        """If the reachability check times out, ConnectionError is raised."""
        requests_mock.get(
            "http://localhost:8080/v1/about",
            exc=requests.exceptions.Timeout,
        )

        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        with pytest.raises(ConnectionError):
            notifier.send(_sample_triage())

    def test_unhealthy_container_raises_connection_error(self, requests_mock):
        """A non-2xx response from /v1/about is treated as an unreachable container."""
        requests_mock.get("http://localhost:8080/v1/about", status_code=503)

        notifier = SignalNotifier(sender="+10000000001", recipient="+10000000002")
        with pytest.raises(ConnectionError):
            notifier.send(_sample_triage())
