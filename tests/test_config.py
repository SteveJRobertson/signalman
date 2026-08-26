"""Tests for the centralised Settings object.

No mocking required — Settings.from_env() reads directly from
os.environ, so these tests just set/unset variables via monkeypatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config import Settings


def _set_required(monkeypatch, sender="+10000000001", recipient="+10000000002") -> None:
    monkeypatch.setenv("SIGNAL_SENDER_NUMBER", sender)
    monkeypatch.setenv("SIGNAL_RECIPIENT_NUMBER", recipient)


# ---------------------------------------------------------------------------
# Tests: required fields
# ---------------------------------------------------------------------------

class TestRequiredFields:
    def test_missing_signal_sender_raises_keyerror(self, monkeypatch):
        monkeypatch.delenv("SIGNAL_SENDER_NUMBER", raising=False)
        monkeypatch.setenv("SIGNAL_RECIPIENT_NUMBER", "+10000000002")

        with pytest.raises(KeyError):
            Settings.from_env()

    def test_missing_signal_recipient_raises_keyerror(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_SENDER_NUMBER", "+10000000001")
        monkeypatch.delenv("SIGNAL_RECIPIENT_NUMBER", raising=False)

        with pytest.raises(KeyError):
            Settings.from_env()

    def test_both_present_succeeds(self, monkeypatch):
        _set_required(monkeypatch)
        settings = Settings.from_env()
        assert settings.signal_sender == "+10000000001"
        assert settings.signal_recipient == "+10000000002"


# ---------------------------------------------------------------------------
# Tests: defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_defaults_used_when_optional_vars_absent(self, monkeypatch):
        _set_required(monkeypatch)
        for var in (
            "SIGNAL_API_URL", "GMAIL_TOKEN_PATH", "GMAIL_CREDENTIALS_PATH",
            "GMAIL_OAUTH_PORT", "OLLAMA_URL", "OLLAMA_MODEL",
        ):
            monkeypatch.delenv(var, raising=False)

        settings = Settings.from_env()

        assert settings.signal_api_url == "http://localhost:8080"
        assert settings.gmail_token_path == Path("token.json")
        assert settings.gmail_credentials_path == Path("credentials.json")
        assert settings.gmail_oauth_port == 8085
        assert settings.ollama_url == "http://localhost:11434"
        assert settings.ollama_model == "llama3"


# ---------------------------------------------------------------------------
# Tests: custom values from env
# ---------------------------------------------------------------------------

class TestCustomValues:
    def test_custom_signal_api_url(self, monkeypatch):
        _set_required(monkeypatch)
        monkeypatch.setenv("SIGNAL_API_URL", "http://192.168.1.10:8080")
        assert Settings.from_env().signal_api_url == "http://192.168.1.10:8080"

    def test_custom_gmail_paths(self, monkeypatch):
        _set_required(monkeypatch)
        monkeypatch.setenv("GMAIL_TOKEN_PATH", "/tmp/custom-token.json")
        monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", "/tmp/custom-creds.json")
        settings = Settings.from_env()
        assert settings.gmail_token_path == Path("/tmp/custom-token.json")
        assert settings.gmail_credentials_path == Path("/tmp/custom-creds.json")

    def test_custom_gmail_oauth_port(self, monkeypatch):
        _set_required(monkeypatch)
        monkeypatch.setenv("GMAIL_OAUTH_PORT", "9999")
        assert Settings.from_env().gmail_oauth_port == 9999

    def test_custom_ollama_url(self, monkeypatch):
        _set_required(monkeypatch)
        monkeypatch.setenv("OLLAMA_URL", "http://192.168.1.10:11434")
        assert Settings.from_env().ollama_url == "http://192.168.1.10:11434"

    def test_custom_ollama_model(self, monkeypatch):
        _set_required(monkeypatch)
        monkeypatch.setenv("OLLAMA_MODEL", "mistral")
        assert Settings.from_env().ollama_model == "mistral"


# ---------------------------------------------------------------------------
# Tests: numeric coercion failure
# ---------------------------------------------------------------------------

class TestNumericCoercion:
    def test_non_numeric_oauth_port_raises_value_error(self, monkeypatch):
        _set_required(monkeypatch)
        monkeypatch.setenv("GMAIL_OAUTH_PORT", "not-a-number")

        with pytest.raises(ValueError, match="GMAIL_OAUTH_PORT"):
            Settings.from_env()


# ---------------------------------------------------------------------------
# Tests: OLLAMA_URL breaking-change guard
# ---------------------------------------------------------------------------

class TestOllamaUrlGuard:
    def test_base_url_accepted(self, monkeypatch):
        _set_required(monkeypatch)
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
        assert Settings.from_env().ollama_url == "http://localhost:11434"

    def test_full_generate_endpoint_rejected(self, monkeypatch):
        """The old OLLAMA_URL form (a full /api/generate endpoint) must fail
        loudly at startup rather than 404 mid-run."""
        _set_required(monkeypatch)
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434/api/generate")

        with pytest.raises(ValueError, match="OLLAMA_URL"):
            Settings.from_env()

    def test_full_generate_endpoint_with_trailing_slash_rejected(self, monkeypatch):
        _set_required(monkeypatch)
        monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434/api/generate/")

        with pytest.raises(ValueError, match="OLLAMA_URL"):
            Settings.from_env()
