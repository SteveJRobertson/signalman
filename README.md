# Signalman

A local, AI-powered Gmail triage agent that fetches your unread emails, classifies them with a local LLM (Ollama), and delivers a structured daily briefing to your phone via Signal.

---

## How it works

```
Gmail API → provider_gmail.py → processor_ai.py (Ollama) → notifier_signal.py → Signal
```

1. **Gmail Fetcher** (`provider_gmail.py`) – authenticates with OAuth 2.0 and retrieves unread emails from the last 24 hours.
2. **AI Processor** (`processor_ai.py`) – sends each email to a local Ollama LLM (one call per email) and returns a verdict per email: `urgent`, `tasks`, `digest`, or `ignore` (junk, left out of the briefing).
3. **Signal Notifier** (`notifier_signal.py`) – formats the verdicts (and flags any email that could not be fetched or classified) and delivers it via the Signal REST API.
4. **Orchestrator** (`main.py`) – wires the three modules together, reads configuration from `.env`, waits for Ollama and Signal to be ready, and sends you a Signal alert if a run fails ([details](#when-something-goes-wrong)).

---

## Setup

### Prerequisites

| Dependency   | Install                                                       |
| ------------ | ------------------------------------------------------------- |
| Python 3.11+ | `brew install python`                                         |
| Ollama       | [ollama.com/download](https://ollama.com/download)            |
| Docker       | [docker.com/get-started](https://www.docker.com/get-started/) |

---

### 1. Clone the repository

```bash
git clone https://github.com/SteveJRobertson/signalman.git
cd signalman
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Pull your Ollama model

```bash
ollama pull llama3
```

Make sure Ollama is running before each scheduled run (it starts automatically on macOS if you installed the desktop app).

---

### 5. Set up Gmail API credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or use an existing one).
3. Enable the **Gmail API**.
4. Create **OAuth 2.0 credentials** (Desktop application type).
5. Download the credentials JSON file and save it to the project root as `credentials.json`.

On first run the consent flow starts a local listener on `GMAIL_OAUTH_PORT` (default `8085`) and **prints a URL to the terminal** rather than opening a browser itself — so the flow also works over SSH. Open the printed URL in any browser, approve access within `GMAIL_OAUTH_TIMEOUT` seconds (default 5 minutes), and the resulting token is saved automatically as `token.json`, then refreshed on subsequent runs.

Do this first run **from a terminal**. Without one, as under launchd, Signalman never starts the consent flow, because nobody could complete it. It alerts you instead ([details](#when-something-goes-wrong)).

Add `http://127.0.0.1:8085/` to the **Authorised redirect URIs** of your OAuth client in the Google Cloud console. If you change `GMAIL_OAUTH_PORT`, change the redirect URI to match.

> `credentials.json` and `token.json` are gitignored. `token.json` is a long-lived key to your Gmail account, saved readable only by your user — never commit it, and revoke it at [myaccount.google.com/permissions](https://myaccount.google.com/permissions) if it is ever exposed.

---

### 6. Start the Signal API container

Run the Signal REST API Docker container, mounting your existing signal-cli data directory:

```bash
docker run -d --name signal-api \
  -p 8080:8080 \
  -v ~/.local/share/signal-cli:/home/.local/share/signal-cli \
  -e "MODE=json-rpc" \
  bbernhard/signal-cli-rest-api
```

The container exposes the API on `http://localhost:8080`. It needs to be running for each scheduled run, though Signalman allows it about a minute to start ([details](#when-something-goes-wrong)).

---

### 7. Create the `.env` file

Copy the example below and fill in your values. The `.env` file is **gitignored** and must never be committed.

```dotenv
# Required
SIGNAL_SENDER_NUMBER=+447700900000     # The Signal number registered in the Docker container
SIGNAL_RECIPIENT_NUMBER=+447700900001  # Your personal phone number (the number that will receive briefings)

# Optional – override defaults only if needed
GMAIL_TOKEN_PATH=token.json
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_OAUTH_PORT=8085                                  # Local port for the one-off OAuth consent flow
GMAIL_MAX_EMAILS=500                                   # Safety cap per run; a warning is logged if it hides mail
GMAIL_OAUTH_TIMEOUT=300                                # Seconds to wait for you to approve the consent URL
MAX_BODY_CHARS=6000                                    # Longest cleaned email body sent to the model
OLLAMA_NUM_CTX=8192                                    # Context window requested from Ollama per email
OLLAMA_TIMEOUT=120                                     # Seconds to wait for Ollama per email
OLLAMA_RETRIES=2                                       # Extra attempts per email after a failed one (1s, then 2s, apart)
READINESS_ATTEMPTS=6                                   # Times to check Ollama and Signal are up before giving up
READINESS_INTERVAL=10                                  # Seconds between those checks
SIGNAL_API_URL=http://localhost:8080                   # Base URL of the Signal REST API container
OLLAMA_URL=http://localhost:11434                      # Ollama BASE url — not the full /api/generate endpoint
OLLAMA_MODEL=llama3
```

---

### 8. Test the script manually

```bash
python3 main.py
```

You should receive a Signal message within a few seconds. Check the terminal for any errors.

#### Preview mode

To see the briefing without messaging your phone — for example while tuning the classification prompt — use the flags below. Gmail and Ollama are still contacted; Signal is not (the container does not even need to be running). If the run fails, the alert is printed rather than sent.

| Flag        | Effect                                                                    |
| ----------- | ------------------------------------------------------------------------- |
| `--dry-run` | Print the briefing to stdout; make no Signal call                         |
| `--limit N` | Process at most N emails (also caps how many are fetched from Gmail)      |
| `--verbose` | DEBUG logging, including each AI prompt and the model's raw reply        |

```bash
python3 main.py --dry-run --limit 10 --verbose
```

`--verbose` applies to Signalman's own modules only, so it does not log OAuth tokens, but its output does include the content of your emails. Treat any saved copy as private.

---

### 9. Schedule with launchd (runs every day at 08:00)

1. **Edit `signalman_daily.plist`** – replace all occurrences of `/path/to/signalman` with the absolute path to your clone, and replace `yourusername` with your macOS username:

   ```bash
   # Example paths after editing:
   # /Users/steve/Projects/signalman/.venv/bin/python
   # /Users/steve/Projects/signalman/main.py
   # /Users/steve/Library/Logs/signalman/signalman.log
   ```

2. **Create the log directory:**

   ```bash
   mkdir -p ~/Library/Logs/signalman
   ```

3. **Copy the plist to LaunchAgents:**

   ```bash
   cp signalman_daily.plist ~/Library/LaunchAgents/com.signalman.daily.plist
   ```

4. **Load the job:**

   ```bash
   launchctl load ~/Library/LaunchAgents/com.signalman.daily.plist
   ```

5. **Verify it is registered:**

   ```bash
   launchctl list | grep signalman
   ```

6. **Run it immediately to test:**

   ```bash
   launchctl start com.signalman.daily
   ```

7. **To unload / disable:**

   ```bash
   launchctl unload ~/Library/LaunchAgents/com.signalman.daily.plist
   ```

---

## When something goes wrong

Signalman runs unattended, so it is built to tell you when it breaks.

**It waits for its services.** Before touching your mail, it checks that Ollama and the Signal container are answering, up to 6 times, 10 seconds apart (`READINESS_ATTEMPTS`, `READINESS_INTERVAL`). At 08:00 after a restart, these are often still starting. If Docker takes longer than a minute to come up on your machine, raise these.

**It alerts you if a run fails.** You get a Signal message saying what went wrong and when:

```
🔴 Signalman failed
ConnectionError: Still not ready after 6 attempts: Ollama is unreachable at http://localhost:11434. Make sure Ollama is running.
2026-11-02T08:01:03+00:00
```

| What you see | What it means | What to do |
| ------------ | ------------- | ---------- |
| `ConnectionError: Still not ready…` | Ollama did not come up in time | Start Ollama |
| `ModelNotFoundError` | The model in `OLLAMA_MODEL` is not installed | Run the `ollama pull` command it names |
| `GmailAuthError` | The Gmail sign-in has expired, been revoked, or is missing | Sign in again, as below |
| `OllamaRequestError: …in a row` | Ollama stopped answering partway through | Check that Ollama is healthy |
| ⚠️ *N emails could not be classified* or *fetched*, in a briefing | Those emails are missing from the briefing, but the rest arrived | Check `signalman.log` and `signalman.error.log` if it keeps happening |
| **No message by 09:00** | Something Signalman could not report: Signal itself is down, the machine was off, or the job did not run | Check `signalman.error.log` |

To sign in to Gmail again, run this from a terminal in the project directory, with the virtualenv active. It prints a consent URL, saves the new token, and sends nothing to Signal:

```bash
python3 main.py --dry-run --limit 1
```

### The daily briefing is a heartbeat

**A briefing is sent every day, even when there is nothing to report** ("All clear"). This is deliberate, and it is not to be optimised away.

An alert cannot report that Signal is down, or that the machine never woke up. The daily message covers those cases: because something always arrives, its absence means something is wrong. Stop sending the empty one and a quiet inbox becomes indistinguishable from a broken Signalman.

The digest section lists at most 20 items, then says "…and 47 more". Urgent items and tasks are never capped.

---

## Running the tests

```bash
pytest
```

All tests are fully mocked – no Gmail credentials, Ollama instance, or Signal account are required to run the test suite.

---

## Project structure

```
signalman/
├── main.py                   # Orchestrator
├── provider_gmail.py         # Gmail fetcher
├── cleaner.py                # Strips quoted replies, signatures, footers; bounds length
├── processor_ai.py           # Ollama AI triage
├── notifier_signal.py        # Signal messenger
├── signalman_daily.plist     # macOS launchd schedule
├── requirements.txt
├── .env                      # ← create this (gitignored)
├── credentials.json          # ← add after Google Cloud setup (gitignored)
├── token.json                # ← generated on first run (gitignored)
└── tests/
    ├── test_main.py
    ├── test_provider_gmail.py
    ├── test_cleaner.py
    ├── test_config.py
    ├── test_processor.py
    └── test_notifier.py
```

---

## Environment variables reference

| Variable                  | Required | Default                               | Description                                            |
| ------------------------- | -------- | ------------------------------------- | ------------------------------------------------------ |
| `SIGNAL_SENDER_NUMBER`    | ✅       | –                                     | Signal phone number registered in the Docker container |
| `SIGNAL_RECIPIENT_NUMBER` | ✅       | –                                     | Phone number that receives the briefing                |
| `GMAIL_TOKEN_PATH`        | ❌       | `token.json`                          | Path to the OAuth2 token file                          |
| `GMAIL_CREDENTIALS_PATH`  | ❌       | `credentials.json`                    | Path to the OAuth2 credentials file                    |
| `GMAIL_OAUTH_PORT`        | ❌       | `8085`                                | Local port used by the one-off OAuth consent flow      |
| `GMAIL_MAX_EMAILS`        | ❌       | `500`                                 | Safety cap on emails processed per run (a warning is logged if it truncates) |
| `GMAIL_OAUTH_TIMEOUT`     | ❌       | `300`                                 | Seconds the consent flow waits for you to approve the URL it prints |
| `MAX_BODY_CHARS`          | ❌       | `6000`                                | Longest email body (after cleaning) sent to the model; longer ones are truncated |
| `OLLAMA_NUM_CTX`          | ❌       | `8192`                                | Context window (tokens) requested from Ollama for each email |
| `OLLAMA_TIMEOUT`          | ❌       | `120`                                 | Seconds to wait for Ollama to answer one email |
| `OLLAMA_RETRIES`          | ❌       | `2`                                   | Extra attempts per email after a timeout, connection error, 5xx or unusable reply (never a 4xx) |
| `READINESS_ATTEMPTS`      | ❌       | `6`                                   | Times to check Ollama and Signal are up before a run gives up |
| `READINESS_INTERVAL`      | ❌       | `10`                                  | Seconds between those checks                           |
| `SIGNAL_API_URL`          | ❌       | `http://localhost:8080`               | Base URL of the Signal REST API container              |
| `OLLAMA_URL`              | ❌       | `http://localhost:11434`              | Ollama **base** URL (not the full API endpoint)        |
| `OLLAMA_MODEL`            | ❌       | `llama3`                              | LLM model to use for triage                            |
