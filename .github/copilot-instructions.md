# Signalman — Copilot Instructions

## Project Overview

Signalman is a Python 3.12+ automation that triages Gmail via a local Ollama LLM and delivers a daily briefing via the Signal REST API (Docker container).

Architecture:

```
Gmail API → provider_gmail.py → processor_ai.py (Ollama) → notifier_signal.py → Signal REST API
```

---

## Language & Runtime

- Python 3.12+
- All new code must be compatible with the existing modules: `provider_gmail.py`, `processor_ai.py`, `notifier_signal.py`, `main.py`

---

## Testing

- Test framework: `pytest`
- All tests live in `tests/` and must be fully mocked — no real credentials, running services, or network calls
- Use `requests-mock` (pytest fixture) for mocking HTTP calls; do NOT use `unittest.mock.patch` to mock `requests`
- Every new feature or bug fix must have corresponding tests
- All tests must pass before any change is considered complete: `python3 -m pytest --tb=short`

---

## HTTP & External Services

- Use the `requests` library for all HTTP calls
- Do NOT use `subprocess` to call external CLI tools
- The Signal REST API runs at `http://localhost:8080` by default (configurable via `SIGNAL_API_URL`)
- Ollama runs at `http://localhost:11434` by default (configurable via `OLLAMA_URL`)

---

## Environment Variables

- All configuration is loaded from `.env` via `python-dotenv`
- **Never** commit `.env`, `credentials.json`, or `token.json`
- Required variables must raise `KeyError` on startup if absent (use `os.environ["VAR"]`, not `os.getenv`)
- Optional variables use `os.getenv("VAR", "default")`
- When renaming an env var, **replace** the old name — do not add a new one alongside it

### Current variables

| Variable                  | Required | Default                               |
| ------------------------- | -------- | ------------------------------------- |
| `SIGNAL_SENDER_NUMBER`    | ✅       | –                                     |
| `SIGNAL_RECIPIENT_NUMBER` | ✅       | –                                     |
| `SIGNAL_API_URL`          | ❌       | `http://localhost:8080`               |
| `GMAIL_TOKEN_PATH`        | ❌       | `token.json`                          |
| `GMAIL_CREDENTIALS_PATH`  | ❌       | `credentials.json`                    |
| `GMAIL_OAUTH_PORT`        | ❌       | `8085`                                |
| `OLLAMA_URL`              | ❌       | `http://localhost:11434/api/generate` |
| `OLLAMA_MODEL`            | ❌       | `llama3`                              |

---

## Dependencies

- Add new dependencies to `requirements.txt`
- Do not add optional dev-only packages to the same list as runtime dependencies without a comment

---

## Code Style

- Follow PEP 8
- Use `from __future__ import annotations` at the top of every module
- Prefer explicit keyword arguments at call sites
- Keep modules single-responsibility — do not add Gmail logic to `notifier_signal.py`, etc.

---

## Security

- Validate all external inputs at system boundaries
- Follow OWASP Top 10 guidelines
- Never log credentials, tokens, or phone numbers
