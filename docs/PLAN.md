# Signalman — Technical Plan

Implementation detail for the roadmap in [SPEC.md](SPEC.md). Phases 0a–6 are specified in full; 7–9 are outlined and will be expanded once the core lands.

---

## Decisions taken

| Area | Decision |
| --- | --- |
| Triage architecture | **One AI call per email**, grouped locally |
| Concurrency | **Sequential** — simplicity over wall-clock |
| HTML extraction | **`html2text`** |
| Clutter removal | **Hand-rolled heuristics**, no dependency |
| AI output | **JSON schema constraint + retry**, skip-on-fail |
| Failure alerting | **Signal alert + always-send daily heartbeat** |
| Dedupe state | **JSON file in `~/.local/share/signalman/`**, 30-day retention |
| Settings | **Frozen dataclass**, loaded once at startup |
| Gmail fetch | **Batched requests** |
| Test debt | **Fixed per phase**, as each area is touched |
| CI | **GitHub Actions: pytest + ruff** |

### Two consequences worth stating up front

**Per-email triage collapses Phase 4.** "Never overload the AI" was going to mean batching logic sized against a context window. With one email per call it reduces to bounding a single body and setting the context window explicitly. The hard part disappeared.

**It also forces a contract change.** The processor currently returns `dict[str, list[str]]` — three lists of bare strings, with no link back to the emails that produced them. Per-email classification produces that mapping naturally, and Phase 8 needs it. The contract between processor and notifier changes, and every test touching it moves with it. This is the single largest refactor in the plan; it lands in Phase 4.

---

## Phase 0a — Reconcile the environment (do first)

Two discrepancies between what the documents describe and what exists on the machine.

**Python version.** Every document claims 3.12+. The only interpreter installed is **3.11.4**, and the compiled test artefacts (`__pycache__/*.cpython-311.pyc`) confirm the suite has always run on 3.11. Nothing in the codebase needs 3.12 — the syntax used (`X | None`, `list[dict]`) is 3.10+.

Recommendation: **target 3.11**, correct the four documents to say `3.11+`, and pin CI to 3.11 so it matches reality. Upgrading to 3.12 is fine but should be a deliberate act, not an assumption.

**No virtualenv exists.** `README.md` instructs you to create `.venv/`, and `signalman_daily.plist` hard-codes `/path/to/signalman/.venv/bin/python`. There is no `.venv` — the suite has been running against system Python. **The scheduled job as documented would fail on a fresh machine.** Either create the venv or point the plist at the interpreter actually in use, then confirm the job fires before relying on it.

---

## Phase 0b — Settings object (do before feature work)

Every phase below adds configuration. Scheduled last — as it originally was — this means adding settings the awkward way nine times and then refactoring all nine. Scheduled first, it makes every phase after it cheaper to build and easier to test.

### New: `config.py`

```python
@dataclass(frozen=True, slots=True)
class Settings:
    # required
    signal_sender: str
    signal_recipient: str
    # signal
    signal_api_url: str = "http://localhost:8080"
    # gmail
    gmail_token_path: Path = Path("token.json")
    gmail_credentials_path: Path = Path("credentials.json")
    gmail_oauth_port: int = 8085
    gmail_max_emails: int = 500          # safety cap, Phase 1
    # ollama
    ollama_url: str = "http://localhost:11434"   # BASE url — see note
    ollama_model: str = "llama3"
    ollama_num_ctx: int = 8192
    ollama_timeout: int = 120
    ollama_retries: int = 2
    # content
    max_body_chars: int = 6000
    # state (Phase 7)
    state_path: Path = Path.home() / ".local/share/signalman/seen.json"
    state_retention_days: int = 30
    # behaviour
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> "Settings": ...
```

`from_env()` uses `os.environ[...]` for the two required fields (preserving the documented `KeyError`-on-missing behaviour) and `os.getenv(name, default)` for the rest, with `int()` coercion raising a clear `ValueError` on non-numeric input.

### Breaking change: `OLLAMA_URL` semantics

Today `OLLAMA_URL` is the full endpoint (`http://localhost:11434/api/generate`). Phases 5 and 6 need `/api/tags` as well, so this becomes a **base URL** with endpoints constructed from it.

`AGENTS.md` requires that a renamed variable *replaces* the old one rather than sitting alongside it. Apply that here: change the meaning, update all four documents, and have `from_env()` raise a clear error if the value still ends in `/api/generate` — otherwise the failure mode is a confusing 404 mid-run.

### Changes

- `load_dotenv()` called **once**, in `config.py`. Remove the second call from `provider_gmail.py`.
- Delete the module-level `TOKEN_PATH` / `CREDENTIALS_PATH` / `GMAIL_OAUTH_PORT` constants — the import-time binding footgun documented in `CLAUDE.md`.
- `GmailProvider.from_credentials(settings)`, `AIProcessor(settings)`, `SignalNotifier(settings)` all take the object. Defaults live in exactly one place.
- `main.run(settings)` receives it; `__main__` builds it.

### Tests

- Replace `patch.dict("os.environ", ...)` in `tests/test_main.py` with direct `Settings(...)` construction.
- **Fix the three assertions that cannot fail** (`tests/test_main.py:102`, `:108`, `:114`) — each is written to pass under either calling convention. With a settings object they become straightforward identity assertions.
- New `tests/test_config.py`: required-field `KeyError`, defaults, `int` coercion failure, the `/api/generate` guard.

---

## Phase 0c — Preview mode (do second)

Before any prompt work. Every phase from 4 onward involves tuning the AI, and without preview mode each experiment messages your real phone — which is precisely why the tuning never happens.

`main.py` grows an `argparse` CLI (stdlib — no new dependency):

| Flag | Effect |
| --- | --- |
| `--dry-run` | Print the briefing to stdout; make no Signal call |
| `--limit N` | Process at most N emails |
| `--verbose` | DEBUG logging, including each AI prompt and raw reply |
| `--forget` | Clear the dedupe store (Phase 7) |

`--dry-run` sets `Settings.dry_run`; `SignalNotifier.send()` returns early after logging the formatted message. `--verbose` is what makes prompt iteration practical — without the raw reply visible, tuning classification is guesswork.

---

## Phase 1 — Read the whole inbox

**File:** `provider_gmail.py`

### Pagination

`fetch_unread_emails()` currently reads `results.get("messages", [])` once and discards `nextPageToken`. Wrap in a loop:

```python
page_token = None
refs: list[dict] = []
while True:
    resp = self.service.users().messages().list(
        userId="me", q="is:unread newer_than:1d",
        maxResults=100, pageToken=page_token,
    ).execute()
    refs.extend(resp.get("messages", []))
    page_token = resp.get("nextPageToken")
    if not page_token or len(refs) >= self.settings.gmail_max_emails:
        break
refs = refs[: self.settings.gmail_max_emails]
```

The cap is a safety valve, not a feature. **Log a warning when it truncates** — silently dropping mail is the bug being fixed, and a cap that hides its own effect reintroduces it.

### Batched fetch

Replace the per-message `get()` loop with `service.new_batch_http_request()`, in chunks of 50. Each callback parses one message into the email dict. A per-message failure records the id in a `failed` list and continues — one malformed message must not abort the run.

Batch callbacks fire in arbitrary order; **re-sort to the original id order** afterwards so output is deterministic and testable.

### Tests

- Two-page then three-page pagination via `nextPageToken`
- Cap enforced, and warning logged when it bites
- Batch assembles all messages regardless of callback order
- One failed message in a batch does not lose the other 49

---

## Phase 2 — Understand every email

**File:** `provider_gmail.py`. **New dependency:** `html2text`

`_extract_body()` matches `text/plain` only, so HTML-only mail yields `""`. Restructure to prefer plain text, fall back to HTML:

1. Walk the payload tree collecting **both** the first `text/plain` and the first `text/html` part
2. Return the plain text if non-empty after stripping
3. Otherwise convert the HTML via `html2text` and return that
4. Otherwise `""`

Configure `html2text` for LLM consumption, not human reading:

```python
h = html2text.HTML2Text()
h.ignore_images = True        # alt text is noise
h.ignore_links = True         # URLs burn tokens; Phase 8 supplies the real link
h.body_width = 0              # no hard wrapping
h.ignore_emphasis = True
```

Also collapse runs of 3+ blank lines — table-based newsletter markup produces enormous vertical whitespace.

### Tests

The current suite has **no HTML-only case** — both tests mentioning `text/html` give it a `text/plain` sibling, which is why this gap was invisible. Add:

- HTML-only message produces non-empty text
- Plain text still wins when both parts exist
- Nested multipart with HTML only at depth
- Empty/whitespace-only plain part falls through to HTML
- A realistic table-based newsletter fixture, asserting no runaway blank lines

---

## Phase 3 — Strip the clutter

**New file:** `cleaner.py`. Pure functions, no I/O, no dependencies — the easiest module in the project to test exhaustively.

```python
def clean(body: str, *, max_chars: int) -> str
```

Applied in order:

1. **Quoted replies** — cut at the first match of:
   - `^On .+ wrote:$` (multiline)
   - `^-{3,}\s*Original Message\s*-{3,}$`
   - `^_{10,}$` (Outlook)
   - `^From: .+$` followed within 3 lines by `^Sent: `
   - A line beginning `>` where the following 3+ lines also begin `>`
2. **Signatures** — cut at `^--\s*$` (RFC 3676 delimiter), and at `^(Sent from my (iPhone|iPad|Android)|Get Outlook for)`
3. **Footers** — drop trailing blocks matching unsubscribe / view-in-browser / "you are receiving this" / physical-address patterns
4. **Whitespace** — collapse 3+ newlines to 2, strip trailing spaces
5. **Truncate** to `max_chars` **on a word boundary**, appending `… [truncated]`

**Safety rule: never return empty.** If the heuristics strip everything, return the truncated original. A signature-detection false positive must degrade to noisy input, never to no input — a wrongly-empty body silently removes an email from consideration, which is precisely the failure class this whole plan exists to eliminate.

### Tests

Table-driven over fixtures: Gmail reply chain, Outlook chain, `--` signature, mobile signature, newsletter footer, a body that is *only* a quote (must not empty out), a body with no clutter (must pass through unchanged), truncation on a word boundary.

---

## Phase 4 — Bound the input, restructure the contract

**File:** `processor_ai.py`. The largest change in the plan.

### New contract

```python
Category = Literal["urgent", "tasks", "digest", "ignore"]

@dataclass(frozen=True, slots=True)
class TriagedItem:
    email_id: str
    sender: str
    subject: str
    category: Category
    summary: str

@dataclass(frozen=True, slots=True)
class TriageResult:
    items: tuple[TriagedItem, ...]
    failed_ids: tuple[str, ...]      # classification failed after retries
    skipped_count: int               # already-seen, Phase 7

    def by_category(self, c: Category) -> list[TriagedItem]: ...
```

Note the fourth category. The current prompt instructs the model to "ignore junk" while offering only three buckets — so junk is forced into `digest`. An explicit `ignore` category, filtered out before formatting, gives it somewhere to go.

### Per-email classification

```python
def triage(self, emails: list[dict]) -> TriageResult:
    items, failed = [], []
    for i, email in enumerate(emails, 1):
        logger.info("Classifying %d/%d…", i, len(emails))
        try:
            items.append(self._classify_one(email))
        except ClassificationError:
            failed.append(email["id"])
    return TriageResult(tuple(items), tuple(failed), 0)
```

Sequential and deliberately boring. Progress logging matters: a silent 8-minute run is indistinguishable from a hang.

### Bounding

With one email per call, overflow protection is two settings rather than batching logic:

- Body already truncated to `max_body_chars` (6000 ≈ 1500 tokens) by Phase 3
- **`options.num_ctx` set explicitly to 8192.** This is the crux of the original bug: Ollama defaults `num_ctx` to 2048 regardless of what the model supports, and silently discards the overflow rather than erroring. Verify against the running instance — if the default has changed, the explicit setting is still correct, just less urgent.
- Subject truncated to 200 chars, sender to 100

### Downstream

`notifier_signal.py` `format_message()` takes `TriageResult`. `main.py` logs per-category counts from `by_category()`. All processor and notifier tests move to the new shape.

---

## Phase 5 — Constrain the AI's output

**File:** `processor_ai.py`

### Schema-constrained generation

```python
_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string",
                     "enum": ["urgent", "tasks", "digest", "ignore"]},
        "summary":  {"type": "string"},
    },
    "required": ["category", "summary"],
}

payload = {
    "model": self.settings.ollama_model,
    "prompt": prompt,
    "stream": False,
    "format": _SCHEMA,
    "options": {"num_ctx": self.settings.ollama_num_ctx, "temperature": 0},
}
```

`format` accepting a JSON Schema requires **Ollama ≥ 0.5.0**; confirm the installed version and fall back to `"format": "json"` plus manual key validation if older. `temperature: 0` because classification should be deterministic — the same inbox twice should give the same briefing.

`_normalise_list()` is no longer needed for the shape it was defending against, but keep an equivalent guard: schema constraint makes malformed output very unlikely, not impossible.

### Retry

Wrap `_call_ollama` in a bounded retry (`ollama_retries`, default 2, 1s then 2s backoff) on timeout, connection error, 5xx, or schema-validation failure. Do **not** retry 4xx — a bad request will fail identically. After exhaustion raise `ClassificationError`; Phase 4's loop records the id and continues.

Guard `response.json()["response"]` — currently an unhandled `KeyError` if the response shape differs.

Surface failures in the briefing: *"⚠️ 3 emails could not be classified"*. A silently short briefing is the failure mode being designed out.

### Tests

- Request carries the schema and `num_ctx`
- Retry then success; retry exhausted → `ClassificationError`
- 4xx not retried
- One failed email does not abort a batch of ten
- Malformed response shape handled

---

## Phase 6 — Fail loudly

**Files:** `main.py`, `notifier_signal.py`

### Readiness gate

Before fetching, poll both services — Ollama `/api/tags`, Signal `/v1/about` — up to 6 times at 10s intervals. At 08:00 after a restart, Docker and Ollama are frequently not yet up; a fixed one-minute grace turns the most common real failure into a non-event.

Also assert the configured model appears in `/api/tags`. A missing model currently surfaces as an opaque mid-run error; catching it at startup names the actual problem.

### Failure alerting

`__main__` catches, then attempts a Signal alert:

```
🔴 Signalman failed
<ExceptionType>: <message>
<ISO timestamp>
```

If that send *also* fails, log and exit 1 — nothing further is possible, and the heartbeat covers it.

### Heartbeat

A briefing sends **every day**, including an empty one ("All clear"), which `format_message()` already does. This makes absence meaningful: no message by 09:00 means something is wrong, covering the case where Signal itself is the broken component. Document this in the README as an intended property, not an accident — otherwise someone will later "optimise away" the empty-inbox message and silently remove the safety net.

### Message length

Signal handles long messages, but a 200-item digest is unusable. Cap the digest at 20 items with *"…and 47 more"*; never cap urgent or tasks.

### Tests

- Readiness retries then succeeds; exhausts and raises
- Missing model detected at startup
- Failure path sends an alert
- Alert-send failure exits 1 without raising
- Digest cap applied, urgent never capped

---

## Phases 7–9 — Outline

Expand once 1–6 land.

### Phase 7 — Dedupe

`state.py`, JSON at `~/.local/share/signalman/seen.json`:

```json
{"version": 1, "seen": {"<gmail-message-id>": "2026-08-26T08:00:12Z"}}
```

- Filter **after** fetch, **before** triage — skipping an already-seen email saves an entire AI call
- Record only after a **successful** send, or a crash mid-run permanently loses a day's mail
- Prune entries older than `state_retention_days` on write
- Atomic write: temp file + `os.replace`
- Create parent directory on first run; corrupt/unreadable state logs a warning and starts empty rather than crashing
- `--forget` clears it
- `TriageResult.skipped_count` populated here

### Phase 8 — Actionable items

- Deep link: `https://mail.google.com/mail/u/0/#all/<message_id>` — the API message id works directly in the `#all/` fragment
- Format each item as sender, summary, link
- Verify link behaviour on Signal's iOS client before committing to the format

### Phase 9 — Remaining hygiene

- **CI:** GitHub Actions on push and PR — `pytest` + `ruff check` + `ruff format --check`, Python **3.11**
- **ruff:** config in a new `pyproject.toml` containing only `[tool.ruff]` (deliberately no `[project]` table — the flat top-level modules must not become a package, or `import main` in the tests breaks)
- Merge `AGENTS.md` and `.github/copilot-instructions.md`; they differ only in their H1 and will otherwise drift
- Add `.env.example`
- Remove the stale `signal-cli` PATH comment from `signalman_daily.plist`
- Correct `3.12+` → `3.11+` across all four documents
- Rewrite the four commits authored under the former employer's address

---

## Sequencing

```
0a Environment ──▶ 0b Settings ──▶ 0c Preview ──▶ 1 Pagination ──▶ 2 HTML
                                                                      │
                                                                      ▼
                        4 Contract ◀── 3 Cleaning ◀──────────────────┘
                            │
                            ▼
                        5 Schema ──▶ 6 Fail loudly ──▶ 7 Dedupe ──▶ 8 Links ──▶ 9 Hygiene
```

**0a before everything** — the documented Python version and virtualenv do not exist, so anything built against them is built on an assumption. Cheapest possible task, and it unblocks honest CI work later.
**0b next** — each later phase adds settings; doing it last means doing it nine times over.
**0c second** — the AI's classification prompt is where this tool's quality lives, and it is tuned by trial and error. Without preview mode every experiment messages your phone, so the tuning never happens.
**1→2→3 before 4** — each changes how much text reaches the model, which is what Phase 4 bounds.
**5 immediately after 4** — the per-email loop multiplies exposure to malformed replies.
**6 before 7** — know the pipeline is sound before teaching it to remember its own output.

---

## Dependencies

```diff
  # Runtime dependencies
  google-api-python-client
  google-auth-oauthlib
  google-auth-httplib2
  python-dotenv
  requests
+ html2text

  # Dev / test dependencies
  pytest
  requests-mock
+ ruff
```

## Definition of done

1. `python3 -m pytest --tb=short` green, with real coverage of HTML-only mail, pagination, cleaning, and retry
2. `ruff check` and `ruff format --check` clean
3. `python3 main.py --dry-run` prints a correct briefing against a live inbox of 100+ unread, making no Signal call
4. Second consecutive real run produces an empty briefing (dedupe verified)
5. Stopping the Signal container produces a clear startup error, not a stack trace
6. Stopping Ollama produces a Signal failure alert
7. No claim in `README.md`, `AGENTS.md`, `.github/copilot-instructions.md` or `docs/SPEC.md` contradicts observed behaviour
