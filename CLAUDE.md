# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Signalman triages unread Gmail from the last 24 hours with a local Ollama LLM and delivers a daily briefing over the Signal REST API. It runs on macOS via a `launchd` job (`signalman_daily.plist`, 08:00 daily) — there is no server, no package, and no CI.

## Commands

```bash
source .venv/bin/activate          # venv is expected at .venv/ (launchd plist points at .venv/bin/python)
pip install -r requirements.txt    # runtime + dev deps live in one file, separated by comments

python3 -m pytest --tb=short       # full suite — must pass before any change is complete
python3 -m pytest tests/test_processor.py::TestTriageCombined::test_empty_email_list_returns_empty_categories  # single test
python3 -m pytest -k empty         # by keyword

python3 main.py                    # real run: needs Ollama, the Signal container, and .env
```

There is no linter or formatter configured, and no `pyproject.toml`/`setup.py`/`conftest.py`. Modules are flat top-level imports, so **pytest must be run from the repo root** — `tests/test_main.py` does `import main` and `runpy.run_module("main")`.

Two live dependencies for a real run: Ollama (`ollama pull llama3`) and the Signal container:

```bash
docker run -d --name signal-api -p 8080:8080 \
  -v ~/.local/share/signal-cli:/home/.local/share/signal-cli \
  -e "MODE=json-rpc" bbernhard/signal-cli-rest-api
```

## Architecture

`main.py` is a linear orchestrator over three single-responsibility modules:

```
GmailProvider.fetch_unread_emails() → AIProcessor.triage() → SignalNotifier.send()
```

Two dict shapes are the contract between every module and every test — change one and all four modules plus their tests move together:

- **Email**: `{"id", "subject", "sender", "body"}` — all strings, missing values become `""`, never `None`.
- **Triage**: `{"urgent": [str], "tasks": [str], "digest": [str]}` — always all three keys, always lists of strings.

Things that are easy to get wrong:

- **All settings live in one place: `config.py`'s `Settings` dataclass**, built once via `Settings.from_env()` in `main.py`'s `__main__` block and passed as a single argument into `GmailProvider.from_credentials()`, `AIProcessor()`, and `SignalNotifier()`. There is no per-module env reading and no duplicated defaults — if you're tempted to add a new `os.getenv(...)` call inside one of those modules, add a field to `Settings` instead.
- **`OLLAMA_URL` is a base URL** (e.g. `http://localhost:11434`), not a full endpoint — `AIProcessor` appends `/api/generate` itself. `Settings.from_env()` raises `ValueError` at startup if the value still ends in `/api/generate`, since that was the pre-Phase-0b form and silently 404s otherwise.
- **LLM output is untrusted in shape, not just content.** `processor_ai._normalise_list()` coerces whatever the model returns (bare string, `None`, int, tuple) into `list[str]`. Keep new LLM-derived fields going through it. Prompt injection from email bodies is a documented, accepted risk for this local personal tool — the mitigation is that everything is stringified.
- **`SignalNotifier.send()` probes `GET /v1/about` first** and raises `ConnectionError` if the container is down, before `POST /v2/send`. Both calls must be mocked in tests.
- **Gmail OAuth is headless.** `flow.run_local_server(open_browser=False)` on a fixed port (`GMAIL_OAUTH_PORT`, default 8085) so the flow works over SSH; the redirect URI must match in the Google Cloud console.
- `GmailProvider._extract_body()` recurses through nested multipart payloads and returns the first `text/plain` part it finds.

## Conventions

`AGENTS.md` and `.github/copilot-instructions.md` hold the authoritative rules and are identical apart from their H1 title — **edit both together** when a convention changes, and keep the env-var table in `README.md` in sync too. (Scheduled to be merged into one file in Phase 9.)

- Python 3.11+; `from __future__ import annotations` at the top of every module; PEP 8; explicit keyword arguments at call sites.
- Required settings use `os.environ["VAR"]` inside `Settings.from_env()` so a missing one raises `KeyError` (caught in `main.py`'s `__main__` block → `sys.exit(1)`). Optional ones use `os.getenv("VAR", default)`. When renaming an env var, replace the old name — never add alongside.
- All HTTP goes through `requests`. Never shell out via `subprocess` to a CLI tool.
- Tests are fully mocked: no credentials, no network, no running services. Mock HTTP with the `requests-mock` pytest fixture — do **not** `unittest.mock.patch` `requests` itself. (`unittest.mock` is fine for the Gmail service object and for the module-level classes in `test_main.py`.)
- Never log credentials, tokens, or phone numbers. `.env`, `credentials.json`, and `token.json` are gitignored and must stay that way.

## Planned work

The codebase is mid-rework. Before changing behaviour, read these — several documented conventions above are scheduled to change:

- **`TASKS.md`** (root) — the working checklist. Start here.
- **`docs/SPEC.md`** — non-technical roadmap: what each phase delivers and why it sits where it does.
- **`docs/PLAN.md`** — technical requirements, decisions taken, and the sequencing rationale.

One change still pending that will invalidate part of this file when it lands (Phase 4): `AIProcessor.triage()` stops returning `dict[str, list[str]]` in favour of a `TriageResult` carrying per-email mapping — the "Two dict shapes" contract above becomes one dict shape plus a result object. Update the Architecture section with it.

`SIGNALMAN_SPEC.md` is the original MVP spec, superseded by `docs/SPEC.md` and kept as a historical record. `.github/agents/code-review.agent.md` defines a read-only pre-merge review agent.
