# Signalman — Task Breakdown

Working checklist derived from [docs/PLAN.md](docs/PLAN.md). The *why* behind each phase is in [docs/SPEC.md](docs/SPEC.md).

**Sizes:** `S` under an hour · `M` one to three hours · `L` half a day or more.
Test tasks are listed separately from implementation on purpose — they are the deliverable, not the afterthought.

| Phase | Tasks | Status |
| --- | --- | --- |
| 0 · Secure the keys | 4 | ✅ Done |
| 0a · Reconcile the environment | 4 | ✅ Done |
| 0b · Settings object | 13 | ✅ Done |
| 0c · Preview mode | 6 | ✅ Done |
| 1 · Read the whole inbox | 10 | ✅ Done |
| 2 · Understand every email | 9 | ✅ Done |
| 3 · Strip the clutter | 12 | ✅ Done |
| 4 · Bound input, new contract | 12 | ✅ Done |
| 5 · Constrain AI output | 12 | ✅ Done |
| 6 · Fail loudly | 11 | ✅ Done |
| 7–9 · Improvement | 21 | ⬜ Outline only |

**Do not reorder 0a → 0b → 0c → 1 → 2 → 3 → 4 → 5 → 6.** The dependency reasoning is in the plan's sequencing section; the short version is that each phase changes the input the next one is sized against. The three 0-phases are groundwork: nothing *depends* on them, but everything *uses* them, which is why they come first rather than last.

---

## Phase 0 · Secure the keys ✅

- [x] **Ignore the secrets** — `credentials.json`, `token.json`, `.DS_Store` · `.gitignore` · `S`
- [x] **Document `GMAIL_OAUTH_PORT`** — absent from all three env tables · `README.md`, `AGENTS.md`, `.github/copilot-instructions.md` · `S`
- [x] **Correct the OAuth flow description** — README claimed a browser opens; `open_browser=False` means it prints a URL · `README.md` · `S`
- [x] **Mark the old spec superseded** · `SIGNALMAN_SPEC.md` · `S`

---

## Phase 0a · Reconcile the environment ✅

Four documents described an environment that didn't exist. Settled before writing code against it.

- [x] **Decide the Python version** — only 3.11.4 is installed; the suite has always run on it; nothing in the code needs 3.12 · `S`
- [x] **Correct the version claim** — `3.12+` → `3.11+` in four places · `README.md`, `AGENTS.md`, `.github/copilot-instructions.md`, `SIGNALMAN_SPEC.md` · `S`
- [x] **Create `.venv` and install deps** — the README instructs it and the plist hard-codes it; created and verified (72 tests pass inside it) · `S`
- [x] **Verify the scheduled job actually runs** — loaded a personalised copy of the plist into `~/Library/LaunchAgents`, triggered it with `launchctl start`, confirmed via the log that it invoked the `.venv` interpreter in the correct working directory and failed cleanly on a missing env var (expected — no credentials are configured yet). Unloaded and removed afterwards; nothing left registered on the system · `S`

---

## Phase 0b · Settings object ✅

Every phase below adds configuration; doing this last would mean doing it nine times over, then redoing all nine.

### Implementation

- [x] **Create `config.py`** — frozen `Settings` dataclass, all fields and defaults from the plan · `M`
- [x] **Write `from_env()`** — `os.environ[...]` for the two required fields, `os.getenv` for the rest · `M`
- [x] **Coerce numerics safely** — `int()` failure must name the offending variable, not raise a bare `ValueError` · `S`
- [x] **Migrate `OLLAMA_URL` to a base URL** — breaking change; Phases 5–6 need `/api/tags` too · `S`
- [x] **Guard the old `OLLAMA_URL` form** — a value ending `/api/generate` must fail at startup with a clear message, not a mid-run 404 · `S`
- [x] **Single `load_dotenv()`** — remove the duplicate call from `provider_gmail.py` · `S`
- [x] **Delete the import-time constants** — `TOKEN_PATH`, `CREDENTIALS_PATH`, `GMAIL_OAUTH_PORT` bind at import and cannot be overridden afterwards · `provider_gmail.py` · `S`
- [x] **Thread `Settings` through all four modules** — constructor argument on each · `M`

### Tests

- [x] **New `tests/test_config.py`** — required-field `KeyError`, defaults, bad-int message, `/api/generate` guard · `M`
- [x] **Replace env patching in `tests/test_main.py`** — construct a `Settings` and pass it; no `patch.dict` · `M`
- [x] **Fix the three assertions that cannot fail** — `tests/test_main.py:102`, `:108`, `:114`. Resolved by removing them rather than patching in place: each was checking one constructor kwarg (`sender`, `recipient`, `api_url`) individually, using `kwargs.get(x) or …` fallbacks to tolerate either calling convention — which is exactly why they could pass on wrong data. Now that every constructor takes a single `settings` argument, one `assert_called_once_with(settings)` per constructor replaces all three and is strictly stronger · `S`

### Docs

- [x] **Document the `OLLAMA_URL` change** — three env tables plus the dotenv example · `S`
- [x] **Note the settings object** in `CLAUDE.md` — replaced the two-paradigm/duplicated-defaults gotchas with a description of `config.py`; also corrected two things found stale while editing: `CLAUDE.md`'s own leftover `3.12+` claim (missed in Phase 0a, which only swept the other four docs), and its "byte-identical" claim about `AGENTS.md`/`.github/copilot-instructions.md` (they differ in the H1 only) · `S`

---

## Phase 0c · Preview mode ✅

Do this before touching the classification prompt. Without it, every experiment messages your real phone, which is exactly why prompt tuning never happens.

- [x] **Add an `argparse` CLI to `main.py`** — stdlib, no dependency · `M`
- [x] **`--dry-run`** — print the briefing, make no Signal call (the notifier prints and returns before the reachability probe) · `S`
- [x] **`--limit N`** — cap emails processed, for fast iteration · `S`
- [x] **`--verbose`** — DEBUG logging including each prompt and raw reply. This is the flag that makes prompt tuning possible · `S`
- [x] **Test: `--dry-run` issues no HTTP request** — assert against `requests_mock` call count · `S`
- [x] **Document preview mode** in the README · `S`

---

Also done in this phase: the `GmailProvider` now takes `max_emails` (from `Settings.gmail_max_emails`) and warns when it truncates, so `--limit` saves fetch time; and the `runpy` tests in `tests/test_main.py` no longer depend on a local `.env` or on pytest's `sys.argv`.

---

## Phase 1 · Read the whole inbox ✅

### Implementation · `provider_gmail.py`

- [x] **Paginate on `nextPageToken`** — currently read once and discarded, so everything past the first 100 is invisible · `M`
- [x] **Apply `gmail_max_emails` cap** · `S`
- [x] **Warn when the cap truncates** — a cap that hides its own effect recreates the bug being fixed · `S`
- [x] **Batch the message fetches** — `new_batch_http_request()`, chunks of 50, replacing 101 round trips with ~3 · `L`
- [x] **Isolate per-message failures** — record the id, continue; one malformed message must not abort the run · `M`
- [x] **Re-sort to original id order** — batch callbacks fire arbitrarily; output must be deterministic · `S`

### Tests

- [x] **Pagination across two and three pages** · `M`
- [x] **Cap enforced and warning logged** · `S`
- [x] **Batch assembles correctly regardless of callback order** · `M`
- [x] **One failed message does not lose the other 49** · `S`

---

Also done in this phase: `GMAIL_MAX_EMAILS` is now read by `Settings.from_env()` (it existed on the dataclass but could never be set; values below 1 are rejected), and documented in the three env tables. `GmailProvider.failed_ids` records any message that could not be fetched or parsed.

---

## Phase 2 · Understand every email ✅

### Implementation · `provider_gmail.py`

- [x] **Add `html2text`** to `requirements.txt` · `S`
- [x] **Restructure `_extract_body`** — collect both the first `text/plain` and first `text/html` while walking the tree · `M`
- [x] **Prefer plain, fall back to HTML** — return plain if non-empty after stripping, else converted HTML · `S`
- [x] **Configure `html2text` for a model, not a human** — `ignore_images`, `ignore_links`, `body_width=0`, `ignore_emphasis` · `S`
- [x] **Collapse excess blank lines** — table-based newsletter markup produces enormous vertical whitespace · `S`

### Tests

The current suite has **no HTML-only case** — both tests mentioning `text/html` give it a `text/plain` sibling, which is why this gap stayed invisible.

- [x] **HTML-only message yields non-empty text** · `S`
- [x] **Plain text still wins when both parts exist** · `S`
- [x] **Nested multipart with HTML only at depth** · `S`
- [x] **Whitespace-only plain part falls through to HTML** · `S`

---

Also done in this phase: after the first real conversion, layout tables left `---` / `|` scaffolding lines in the output. `ignore_tables` fixed that but glued data-table cells together (`ItemPrice`), so scaffolding-only lines are stripped after conversion instead, which keeps `Item| Price` rows readable. The first non-blank part of each type is used (not merely the first part), so an empty or whitespace-only `text/plain` stub no longer shadows a real HTML body.

---

## Phase 3 · Strip the clutter ✅

New `cleaner.py`. Pure functions, no I/O, no dependencies — the most testable module in the project.

### Implementation

- [x] **Create `cleaner.py`** — `clean(body, *, max_chars) -> str` · `S`
- [x] **Quoted-reply patterns** — `On … wrote:`, `----- Original Message -----`, Outlook underscore rule, `From:`/`Sent:` pairs, consecutive `>` lines · `L`
- [x] **Signature patterns** — `--` delimiter (RFC 3676), mobile sign-offs · `M`
- [x] **Footer patterns** — unsubscribe, view-in-browser, "you are receiving this", physical addresses · `M`
- [x] **Whitespace normalisation** — collapse 3+ newlines, strip trailing spaces · `S`
- [x] **Word-boundary truncation** — to `max_chars`, append `… [truncated]` · `S`
- [x] **Never-return-empty rule** — if the heuristics strip everything, return the truncated original · `S`
- [x] **Wire into the pipeline** — after extraction, before classification · `S`

### Tests

- [x] **Fixtures** — Gmail chain, Outlook chain, `--` signature, mobile signature, newsletter footer · `M`
- [x] **Table-driven cases over the fixtures** · `M`
- [x] **A body that is only a quote does not empty out** — the safety rule; a wrongly-empty body silently drops an email from consideration · `S`
- [x] **A clean body passes through unchanged** · `S`

---

Also done in this phase: `MAX_BODY_CHARS` is now read by `Settings.from_env()` (it existed on the dataclass but could not be set; values below 1 are rejected) and documented in the env tables. Decisions worth knowing: truncation counts the `… [truncated]` marker inside `max_chars`, so the bound is hard; footer removal works within blocks, cutting from the first footer-looking line, because html2text puts the footer on lines directly under the last content line (a first version dropped whole blocks and lost "Pay now"); address patterns are case-sensitive so prose such as "call me at 5 on my way" is not mistaken for a street address; a `>` run of four or more lines cuts everything after it, as specced, which drops interleaved inline replies — accepted for now.

---

## Phase 4 · Bound the input, restructure the contract ✅

The largest refactor in the plan. Per-email classification means the processor can no longer return three lists of bare strings.

### Implementation · `processor_ai.py`, `notifier_signal.py`, `main.py`

- [x] **Define `Category`, `TriagedItem`, `TriageResult`** · `M`
- [x] **Add the `ignore` category** — the prompt already says "ignore junk" but offers nowhere to put it, so junk currently lands in `digest` · `S`
- [x] **Write `_classify_one(email)`** — one email, one call, one verdict · `M`
- [x] **Rewrite `triage()` as a sequential loop** — collect verdicts, record failures, continue · `M`
- [x] **Log progress per email** — a silent 8-minute run is indistinguishable from a hang · `S`
- [x] **Bound the fields** — body via Phase 3, subject 200 chars, sender 100 · `S`
- [x] **Set `num_ctx` explicitly** — the original silent-overflow bug; Ollama's default discards the excess rather than erroring · `S`
- [x] **Update `format_message()` to take `TriageResult`** · `M`
- [x] **Filter `ignore` before formatting** · `S`

### Tests

- [x] **Migrate processor tests to the new contract** · `L`
- [x] **Migrate notifier tests to the new contract** · `M`
- [x] **Migrate main tests to the new contract** · `M`

---

Also done in this phase, ahead of its listed phase: **"Surface failures in the briefing"** (Phase 5) — `failed_ids` now exists, and shipping Phase 4 without it would let a total classification failure read as "All clear". Failures with no successful items show only the warning. `ClassificationError` was also introduced here (Phase 5 task "Add `ClassificationError`") because the Phase 4 loop needs something to catch; Phase 5 extends it to retries and response-shape guards. `OLLAMA_NUM_CTX` and `OLLAMA_TIMEOUT` are now read by `Settings.from_env()` and used (the old code hard-coded a 120s timeout). Decisions worth knowing: transport errors are deliberately not caught by the per-email loop — a dead Ollama should abort the run rather than record every email as failed; `TriagedItem` keeps the full subject/sender (only the *prompt* is truncated to 200/100); summaries are collapsed to one line and fall back to the subject if blank; `_normalise_list` is gone — category validation against a fixed set replaces it.

---

## Phase 5 · Constrain the AI's output ✅

### Implementation · `processor_ai.py`

- [x] **Define the JSON schema** — `category` enum plus `summary`, both required · `S`
- [x] **Send `format` and `options` in the payload** · `S`
- [x] **Check the Ollama version** — schema-constrained `format` needs ≥ 0.5.0; fall back to `"format": "json"` with manual key validation if older · `M`
- [x] **Set `temperature: 0`** — the same inbox twice should give the same briefing · `S`
- [x] **Retry wrapper** — `ollama_retries` attempts, 1s then 2s backoff · `M`
- [x] **Retry only what is retryable** — timeout, connection error, 5xx, schema-validation failure. Never 4xx · `S`
- [x] **Add `ClassificationError`** — raised after exhaustion, caught by Phase 4's loop · `S`
- [x] **Guard `response.json()["response"]`** — currently an unhandled `KeyError` if the shape differs · `S`
- [x] **Surface failures in the briefing** — "⚠️ 3 emails could not be classified". A silently short briefing is the failure mode being designed out · `S` _(done in Phase 4)_

### Tests

- [x] **Request carries schema and `num_ctx`** · `S`
- [x] **Retry then success; exhaustion raises; 4xx not retried** · `M`
- [x] **One failed email does not abort a batch of ten** · `S`

---

Decisions worth knowing, checked against a real Ollama 0.40.13:

- **`ollama_retries` means extra attempts**, so the default of 2 gives three attempts in all, which is what "1s then 2s backoff" needs. `OLLAMA_RETRIES` was declared on `Settings` but never read from the environment until now.
- **A retry after an unusable reply is not at temperature 0.** The plan asks for temperature 0 and for retrying unusable replies, and those two cancel out: three identical calls at 0 returned the same reply word for word, so such a retry could never change the answer. The first attempt stays at 0, so a normal run stays deterministic; only a retry after an unusable reply uses `_RETRY_TEMPERATURE` (0.3). Retries after a transport failure stay at 0.
- **A dead Ollama still aborts the run, as Phase 4 decided.** Taken literally, "record the id and continue" after exhaustion would mean a hung Ollama costs 6 minutes per email (120s, three attempts), with nobody told for hours on a large inbox. So three emails in a row with no reply at all raise `OllamaRequestError`, which reaches Phase 6's failure path. A single slow or failing email, or any number of unusable replies, does not abort.
- **A missing `summary` key now counts as unusable**, as the schema requires. A `summary` that is present but blank or null still falls back to the subject, since the verdict itself is fine.
- **Exception messages no longer quote the model's reply.** It echoes email content, and those messages go to `signalman.error.log` at WARNING, which retries would now repeat. `--verbose` still shows the reply.
- **If the version cannot be read, plain JSON mode is used.** An older server would reject a schema for every email, whereas plain JSON works everywhere, and the same validation runs either way.

---

## Phase 6 · Fail loudly ✅

### Implementation · `main.py`, `notifier_signal.py`

- [x] **Readiness gate** — poll Ollama `/api/tags` and Signal `/v1/about`, 6 attempts at 10s. At 08:00 after a restart these are routinely not up yet · `M`
- [x] **Verify the configured model exists** — a missing model currently surfaces as an opaque mid-run error · `S`
- [x] **Send a Signal alert on failure** — exception type, message, timestamp · `M`
- [x] **Handle alert-send failure** — log and exit 1; the heartbeat covers this case · `S`
- [x] **Document the heartbeat as intentional** — the daily empty briefing is the safety net. Undocumented, someone will optimise it away · `S`
- [x] **Cap the digest at 20 items** — "…and 47 more". Never cap urgent or tasks · `S`

### Tests

- [x] **Readiness retries then succeeds** · `M`
- [x] **Readiness exhausts and raises** · `S`
- [x] **Missing model detected at startup** · `S`
- [x] **Failure path sends an alert; alert failure exits 1 without raising** · `M`
- [x] **Digest capped, urgent never capped** · `S`

---

Also done in this phase, because an unattended run could not report them otherwise:

- **An expired or revoked Gmail token.** `creds.refresh` raising used to end the run with only a line in `signalman.error.log`. It now raises `GmailAuthError`, which becomes an alert saying how to sign in again. A token revoked before its expiry only shows up on first use, in `fetch_unread_emails()`, and is handled the same way. Only Google refusing the token counts: a network failure during refresh stays as it is, because signing in again would not help.
- **A missing or corrupt `token.json`.** `flow.run_local_server` had no timeout, so the launchd job would have waited forever and told no one. The flow now only starts if `sys.stdin.isatty()`, and otherwise raises `GmailAuthError` at once. At a terminal it has a timeout (`GMAIL_OAUTH_TIMEOUT`), and those same cases, including a rejected refresh, simply start the consent flow, with no need to delete `token.json` first. Checked with a real `run_local_server` under a pty: it gives up on time, and with stdin from `/dev/null` it exits at once.
- **Gmail refusing mail could read as "All clear".** Phase 1 records per-message fetch failures in `provider.failed_ids`, but only logged them. If Gmail rate-limited a whole inbox, the briefing would have said "All clear", defeating the heartbeat. They now reach the briefing via `TriageResult.fetch_failed_ids`: "⚠️ 2 emails could not be fetched".
- **`except KeyError` wrapped the whole run**, so a `KeyError` from anywhere was logged as "Missing required environment variable", with no alert possible. Settings now load in their own `try`. A bad value such as `READINESS_ATTEMPTS=lots` is logged as invalid configuration. Neither can send an alert, since there may be no recipient yet, so the missing heartbeat covers them.

Decisions worth knowing:

- **The real `/api/tags` lists `llama3:latest`, while the default setting is `llama3`.** A plain comparison would have failed every morning, so names go through `_with_tag()`. A bare name means `:latest` and nothing else, because that is what Ollama would run: `llama3` is not satisfied by `llama3:8b`.
- **A missing model raises at once rather than polling.** One side effect: if Signal is still starting at that moment, the alert fails too, and the missing heartbeat covers it.
- **`send_alert()` skips the `/v1/about` probe.** The POST itself shows whether the alert got through, and a failed probe would only add a second way to lose it.
- **Dry runs never send an alert.** They print it, and they do not wait for Signal, so `--dry-run` stays safe for any real-data testing.
- **Tests can no longer reach a real Signal API by accident.** Now that `__main__` sends a real alert, an unmocked test could otherwise message the user's phone, given that `import main` can load a real `.env`. `tests/test_main.py` and `tests/test_notifier.py` make any unmocked request fail.

Not verified end to end: **an alert actually arriving on a phone**. Delivery is covered by mocked tests, and every failure path was run for real under `--dry-run`, which prints the alert instead, but no real alert has been sent, because that needs the user's say-so. To check it, with Ollama stopped: `python3 main.py`.

---

## Phases 7–9 · Improvement

Outline only. Expand into full tasks once Phase 6 lands — the shape of this work depends on how 1–6 turn out.

### Phase 7 · Dedupe

- [ ] Create `state.py` — JSON store at `~/.local/share/signalman/seen.json` · `M`
- [ ] Filter after fetch, before triage — skipping a seen email saves an entire AI call · `S`
- [ ] Record only after a **successful** send — otherwise a mid-run crash permanently loses a day's mail · `M`
- [ ] Prune entries past `state_retention_days` on write · `S`
- [ ] Atomic write — temp file plus `os.replace` · `S`
- [ ] Create the parent directory on first run · `S`
- [ ] Corrupt state logs a warning and starts empty rather than crashing · `S`
- [ ] Wire up `--forget` · `S`
- [ ] Tests · `M`

### Phase 8 · Actionable items

- [ ] Build the Gmail deep link — `https://mail.google.com/mail/u/0/#all/<message_id>` · `S`
- [ ] Format items with sender, summary and link · `M`
- [ ] **Verify link behaviour on Signal's iOS client before committing to the format** · `S`
- [ ] Tests · `S`

### Phase 9 · Hygiene

- [ ] GitHub Actions workflow — pytest plus ruff, on push and PR · `M`
- [ ] Add `pyproject.toml` with **only** `[tool.ruff]` — no `[project]` table, or the flat modules become a package and `import main` breaks in the tests · `S`
- [ ] Fix what ruff finds on first run · `M`
- [ ] Merge `AGENTS.md` and `.github/copilot-instructions.md` — they differ only in their H1 and will otherwise drift · `S`
- [ ] Add `.env.example` · `S`
- [ ] Remove the stale `signal-cli` PATH comment from `signalman_daily.plist` · `S`
- [ ] Rewrite the four commits authored under the former employer's address · `M`
- [ ] Add coverage reporting — deferred from CI setup; would have made the thin coverage visible far sooner · `M`

---

## Done when

1. `python3 -m pytest --tb=short` green, with real coverage of HTML-only mail, pagination, cleaning and retry
2. `ruff check` and `ruff format --check` clean
3. `python3 main.py --dry-run` prints a correct briefing against 100+ unread, making no Signal call
4. A second consecutive real run produces an empty briefing
5. Stopping the Signal container gives a clear startup error, not a stack trace
6. Stopping Ollama produces a Signal failure alert
7. No claim in any document contradicts observed behaviour
