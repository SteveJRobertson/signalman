# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Signalman triages unread Gmail from the last 24 hours with a local Ollama LLM and delivers a daily briefing over the Signal REST API. It runs on macOS via a `launchd` job (`signalman_daily.plist`, 08:00 daily) — there is no server, no package, and no CI.

## Commands

```bash
source .venv/bin/activate          # venv is expected at .venv/ (launchd plist points at .venv/bin/python)
pip install -r requirements.txt    # runtime + dev deps live in one file, separated by comments

python3 -m pytest --tb=short       # full suite — must pass before any change is complete
python3 -m pytest tests/test_processor.py::TestTriage::test_empty_email_list_returns_empty_result_without_api_call  # single test
python3 -m pytest -k empty         # by keyword

python3 main.py                    # real run: needs Ollama, the Signal container, and .env
python3 main.py --dry-run --limit 5 --verbose   # preview: prints the briefing, no Signal call
```

There is no linter or formatter configured, and no `pyproject.toml`/`setup.py`/`conftest.py`. Modules are flat top-level imports, so **pytest must be run from the repo root** — `tests/test_main.py` does `import main` and `runpy.run_module("main")`.

Two live dependencies for a real run: Ollama (`ollama pull llama3`) and the Signal container:

```bash
docker run -d --name signal-api -p 8080:8080 \
  -v ~/.local/share/signal-cli:/home/.local/share/signal-cli \
  -e "MODE=json-rpc" bbernhard/signal-cli-rest-api
```

## Architecture

`main.py` is a linear orchestrator over three single-responsibility modules, with a readiness gate first and a pure cleaning step between fetch and triage:

```
wait_until_ready() → GmailProvider.fetch_unread_emails() → cleaner.clean() → AIProcessor.triage() → SignalNotifier.send()
```

One dict shape and one result object are the contract between every module and every test — change either and all four modules plus their tests move together:

- **Email** (dict): `{"id", "subject", "sender", "body"}` — all strings, missing values become `""`, never `None`.
- **`TriageResult`** (frozen dataclass in `processor_ai.py`): `items: tuple[TriagedItem, ...]`, `failed_ids: tuple[str, ...]`, `skipped_count: int` (always 0 until Phase 7), `fetch_failed_ids: tuple[str, ...]` (set by `main.run` from `provider.failed_ids`), plus `by_category(c)`. A `TriagedItem` is `(email_id, sender, subject, category, summary)` with `category` one of `urgent | tasks | digest | ignore`. `ignore` items stay in the result (and are counted in logs) but `format_message()` never shows them.

Things that are easy to get wrong:

- **All settings live in one place: `config.py`'s `Settings` dataclass**, built once via `Settings.from_env()` in `main.py`'s `__main__` block (CLI flags `--dry-run`/`--limit` are applied afterwards with `dataclasses.replace`, since `Settings` is frozen) and passed as a single argument into `GmailProvider.from_credentials()`, `AIProcessor()`, and `SignalNotifier()`. There is no per-module env reading and no duplicated defaults — if you're tempted to add a new `os.getenv(...)` call inside one of those modules, add a field to `Settings` instead.
- **`OLLAMA_URL` is a base URL** (e.g. `http://localhost:11434`), not a full endpoint — `AIProcessor` appends `/api/generate` itself. `Settings.from_env()` raises `ValueError` at startup if the value still ends in `/api/generate`, since that was the pre-Phase-0b form and silently 404s otherwise.
- **`AIProcessor.triage()` makes one Ollama call per email, sequentially.** `options.num_ctx` is always sent, because Ollama silently discards context past its default window. `format` carries the JSON schema (or plain `"json"` if `/api/version` is below 0.5.0 or unreadable — compared as numbers, since `"0.40.5" < "0.5.0"` as text).
- **Retries live in `_verdict_with_retries()`:** `ollama_retries` extra attempts, 1s then 2s apart, on timeout, connection error, 5xx or an unusable reply, never on a 4xx. Tests must mock `/api/version` and patch `processor_ai.time.sleep` (`tests/test_processor.py` does both with autouse fixtures). Temperature is 0 on the first attempt, but a retry after an *unusable reply* uses `_RETRY_TEMPERATURE`, because at 0 the model replays the same reply exactly. Once retries run out, `ClassificationError` makes `triage()` record the id in `failed_ids` and carry on. The exception is `OllamaRequestError` (a subclass: no reply at all, rather than an unusable one): `_MAX_REQUEST_FAILURES_IN_A_ROW` of those abort the run, so a dead or hung Ollama reaches the failure path instead of spending hours failing every email.
- **LLM output is untrusted in shape, not just content.** `AIProcessor._parse_response()` requires a JSON object with both keys, whose `category` (case/whitespace-normalised) is in `CATEGORIES`, and coerces `summary` to a single line of text (falling back to the subject if blank). The schema makes a bad reply unlikely, not impossible, so this stays. Keep new LLM-derived fields going through it, and keep the reply itself out of exception messages: it echoes email content, and those messages reach the on-disk log. Prompt injection from email bodies is a documented, accepted risk for this local personal tool — the mitigation is that the category is validated against a fixed set and everything else is stringified.
- **`SignalNotifier.send()` probes `GET /v1/about` first** and raises `ConnectionError` if the container is down, before `POST /v2/send`. Both calls must be mocked in tests. With `settings.dry_run` it prints the message and returns before either call.
- **`main.wait_until_ready()` polls `AIProcessor.check_ready()` (`/api/tags`) and `SignalNotifier.check_ready()` (`/v1/about`)** before Gmail is touched, `READINESS_ATTEMPTS` times, `READINESS_INTERVAL` apart. A `ConnectionError` means keep waiting; `ModelNotFoundError` raises at once, since waiting will not install a model. A real `/api/tags` lists `llama3:latest` while the default setting is `llama3`, so names go through `_with_tag()` before comparing. Dry runs skip the Signal check.
- **Failures reach the user through `main.report_failure()`.** `__main__` builds settings in its own `try` (a failure there has nobody to alert, and a `KeyError` from later on must not read as a missing variable), then any exception from `run()` becomes a `SignalNotifier.send_alert()` and `sys.exit(1)`. `report_failure()` never raises. In a dry run the alert is printed and nothing is sent.
- **The daily briefing is a heartbeat; never suppress the empty one.** No message by 09:00 is how the user learns of failures that an alert cannot report, such as Signal being down. For the same reason, a briefing must not read "All clear" when mail was dropped.
- **Gmail OAuth is headless, and only runs at a terminal.** `flow.run_local_server(open_browser=False, timeout_seconds=…)` on a fixed port (`GMAIL_OAUTH_PORT`, default 8085) so the flow works over SSH; the redirect URI must match in the Google Cloud console. It blocks until the browser redirect arrives, so `provider_gmail._sign_in()` only starts it if `sys.stdin.isatty()`: a launchd job gets a `GmailAuthError` straight away, which becomes an alert. That covers a missing or corrupt token and a refresh Google rejects, including one first rejected in `fetch_unread_emails()`. At a terminal, those same cases just start the consent flow. Network failures during refresh stay as they are, since they do not mean the sign-in was lost. Tests patch `provider_gmail.sys.stdin`.
- **`GmailProvider.fetch_unread_emails()` pages through `messages.list` (100 per page) up to `gmail_max_emails`, then fetches in batches of 50 via `new_batch_http_request()`.** Batch callbacks fire in arbitrary order, so results are re-sorted to listing order; a failed message lands in `provider.failed_ids` and is skipped. Tests use the `FakeBatch` helper in `tests/test_provider_gmail.py` — a bare `MagicMock` service returns no emails.
- **`cleaner.clean(body, *, max_chars)` is pure (no I/O) and applied per email in `main.run`**, not inside the provider or processor. It cuts quoted replies and signatures at the earliest marker, drops trailing footer lines, normalises whitespace, then truncates on a word boundary (total length including the `… [truncated]` marker is ≤ `max_chars`). **Safety rule: it never returns an empty string for a non-empty body** — if the heuristics strip everything it falls back to the truncated original. Any new pattern must keep that property; a wrongly-empty body silently drops an email.
- `GmailProvider._extract_body()` walks nested multipart payloads, prefers the first non-blank `text/plain` part, and falls back to the first non-blank `text/html` part converted by `html2text` (images/links/emphasis dropped, table scaffolding and blank-line runs stripped). Plain text is returned unmodified; cleanup happens next, in `cleaner.py`. An HTML-only test fixture is required for any change here; the old suite had none, which hid the original bug.

## Conventions

`AGENTS.md` and `.github/copilot-instructions.md` hold the authoritative rules and are identical apart from their H1 title — **edit both together** when a convention changes, and keep the env-var table in `README.md` in sync too. (Scheduled to be merged into one file in Phase 9.)

- Python 3.11+; `from __future__ import annotations` at the top of every module; PEP 8; explicit keyword arguments at call sites.
- Required settings use `os.environ["VAR"]` inside `Settings.from_env()` so a missing one raises `KeyError` (caught in `main.py`'s `__main__` block → `sys.exit(1)`). Optional ones use `os.getenv("VAR", default)`. When renaming an env var, replace the old name — never add alongside.
- All HTTP goes through `requests`. Never shell out via `subprocess` to a CLI tool.
- Tests are fully mocked: no credentials, no network, no running services. Mock HTTP with the `requests-mock` pytest fixture — do **not** `unittest.mock.patch` `requests` itself. (`unittest.mock` is fine for the Gmail service object and for the module-level classes in `test_main.py`.)
- **`__main__` now sends a real alert on failure, and `import main` can load a real `.env`** (`load_dotenv()` walks up from the file, which finds the parent checkout's from a worktree). So `tests/test_main.py` and `tests/test_notifier.py` have an autouse `requests_mock` fixture, which makes an unmocked request raise rather than reach Signal; keep it. Any `runpy` test goes through `_arrange_main_block()`, which pins the URLs and `sys.argv`, and patches the shared `provider_gmail` module rather than `main.X`, since `runpy` re-executes `main`'s imports.
- Never log credentials, tokens, or phone numbers, and never raise the root logger to DEBUG: Google's auth libraries log OAuth tokens at that level, so `--verbose` raises only `main._VERBOSE_LOGGERS` (add any new module with a logger there). `.env`, `credentials.json`, and `token.json` are gitignored and must stay that way.

## Planned work

The codebase is mid-rework. Before changing behaviour, read these — several documented conventions above are scheduled to change:

- **`TASKS.md`** (root) — the working checklist. Start here.
- **`docs/SPEC.md`** — non-technical roadmap: what each phase delivers and why it sits where it does.
- **`docs/PLAN.md`** — technical requirements, decisions taken, and the sequencing rationale.

`SIGNALMAN_SPEC.md` is the original MVP spec, superseded by `docs/SPEC.md` and kept as a historical record. `.github/agents/code-review.agent.md` defines a read-only pre-merge review agent.
