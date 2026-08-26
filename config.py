"""Centralised settings for Signalman.

All configuration is read from the environment exactly once, via
:meth:`Settings.from_env`, and passed explicitly to every component from
there. Required variables use ``os.environ[...]`` and so raise ``KeyError``
if absent; optional variables fall back to the defaults below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_SIGNAL_API_URL = "http://localhost:8080"
_DEFAULT_GMAIL_TOKEN_PATH = "token.json"
_DEFAULT_GMAIL_CREDENTIALS_PATH = "credentials.json"
_DEFAULT_GMAIL_OAUTH_PORT = 8085
_DEFAULT_GMAIL_MAX_EMAILS = 500
_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_OLLAMA_MODEL = "llama3"
_DEFAULT_OLLAMA_NUM_CTX = 8192
_DEFAULT_OLLAMA_TIMEOUT = 120
_DEFAULT_OLLAMA_RETRIES = 2
_DEFAULT_MAX_BODY_CHARS = 6000
_DEFAULT_STATE_RETENTION_DAYS = 30


def _env_int(name: str, default: int) -> int:
    """Read *name* from the environment as an int, or return *default*.

    Raises:
        ValueError: If the variable is set but is not a valid integer.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable, fully-resolved configuration for a single run."""

    # required
    signal_sender: str
    signal_recipient: str

    # signal
    signal_api_url: str = _DEFAULT_SIGNAL_API_URL

    # gmail
    gmail_token_path: Path = Path(_DEFAULT_GMAIL_TOKEN_PATH)
    gmail_credentials_path: Path = Path(_DEFAULT_GMAIL_CREDENTIALS_PATH)
    gmail_oauth_port: int = _DEFAULT_GMAIL_OAUTH_PORT
    gmail_max_emails: int = _DEFAULT_GMAIL_MAX_EMAILS

    # ollama — ollama_url is a BASE url (e.g. http://localhost:11434);
    # endpoints are constructed from it, not baked into the value.
    ollama_url: str = _DEFAULT_OLLAMA_URL
    ollama_model: str = _DEFAULT_OLLAMA_MODEL
    ollama_num_ctx: int = _DEFAULT_OLLAMA_NUM_CTX
    ollama_timeout: int = _DEFAULT_OLLAMA_TIMEOUT
    ollama_retries: int = _DEFAULT_OLLAMA_RETRIES

    # content
    max_body_chars: int = _DEFAULT_MAX_BODY_CHARS

    # state
    state_path: Path = Path.home() / ".local/share/signalman/seen.json"
    state_retention_days: int = _DEFAULT_STATE_RETENTION_DAYS

    # behaviour
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        """Build a :class:`Settings` from the current environment.

        Raises:
            KeyError: If a required variable is not set.
            ValueError: If a numeric variable is set but not a valid
                integer, or if ``OLLAMA_URL`` is a full endpoint rather
                than a base URL.
        """
        ollama_url = os.getenv("OLLAMA_URL", _DEFAULT_OLLAMA_URL)
        if ollama_url.rstrip("/").endswith("/api/generate"):
            raise ValueError(
                "OLLAMA_URL must be the Ollama base URL "
                f"(e.g. {_DEFAULT_OLLAMA_URL}), not a full API endpoint. "
                "Remove the trailing '/api/generate'."
            )

        return cls(
            signal_sender=os.environ["SIGNAL_SENDER_NUMBER"],
            signal_recipient=os.environ["SIGNAL_RECIPIENT_NUMBER"],
            signal_api_url=os.getenv("SIGNAL_API_URL", _DEFAULT_SIGNAL_API_URL),
            gmail_token_path=Path(os.getenv("GMAIL_TOKEN_PATH", _DEFAULT_GMAIL_TOKEN_PATH)),
            gmail_credentials_path=Path(
                os.getenv("GMAIL_CREDENTIALS_PATH", _DEFAULT_GMAIL_CREDENTIALS_PATH)
            ),
            gmail_oauth_port=_env_int("GMAIL_OAUTH_PORT", _DEFAULT_GMAIL_OAUTH_PORT),
            ollama_url=ollama_url,
            ollama_model=os.getenv("OLLAMA_MODEL", _DEFAULT_OLLAMA_MODEL),
        )
