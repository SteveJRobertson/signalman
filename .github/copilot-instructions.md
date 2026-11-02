# Signalman — Copilot Instructions

## Project Overview

Signalman is a Python 3.11+ automation that triages Gmail via a local Ollama LLM and delivers a daily briefing via the Signal REST API (Docker container).

Architecture:

```
Gmail API → provider_gmail.py → processor_ai.py (Ollama) → notifier_signal.py → Signal REST API
```

---

## Language & Runtime

- Python 3.11+
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
| `GMAIL_MAX_EMAILS`        | ❌       | `500`                                 |
| `GMAIL_OAUTH_TIMEOUT`     | ❌       | `300`                                 |
| `MAX_BODY_CHARS`          | ❌       | `6000`                                |
| `OLLAMA_NUM_CTX`          | ❌       | `8192`                                |
| `OLLAMA_TIMEOUT`          | ❌       | `120`                                 |
| `OLLAMA_RETRIES`          | ❌       | `2`                                   |
| `READINESS_ATTEMPTS`      | ❌       | `6`                                   |
| `READINESS_INTERVAL`      | ❌       | `10`                                  |
| `OLLAMA_URL`              | ❌       | `http://localhost:11434`              |
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

## Reliability

- Signalman runs unattended, so a failure nobody hears about is the worst kind. After settings load, any failed run must reach `main.report_failure()`, which sends a Signal alert
- **Never suppress the empty briefing.** The daily "All clear" is a heartbeat: no message by 09:00 is how the user learns that something is wrong, even when Signal itself is what broke
- Never let a briefing read as "All clear" when mail was dropped. Emails that could not be fetched or classified must be called out in it
- Never block waiting for a person unless one is at a terminal. The launchd job has none, so `flow.run_local_server` must stay behind the `sys.stdin.isatty()` check in `provider_gmail._sign_in`, and must keep its timeout

---

## Security

- Validate all external inputs at system boundaries
- Follow OWASP Top 10 guidelines
- Never log credentials, tokens, or phone numbers
- Never raise the root logger to DEBUG: Google's auth libraries log OAuth tokens at that level. `--verbose` raises only `main._VERBOSE_LOGGERS`, so add any new module with a logger there
