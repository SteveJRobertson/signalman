# Signalman

A local, AI-powered Gmail triage agent that fetches your unread emails, classifies them with a local LLM (Ollama), and delivers a structured daily briefing to your phone via Signal.

---

## How it works

```
Gmail API → provider_gmail.py → processor_ai.py (Ollama) → notifier_signal.py → Signal
```

1. **Gmail Fetcher** (`provider_gmail.py`) – authenticates with OAuth 2.0 and retrieves unread emails from the last 24 hours.
2. **AI Processor** (`processor_ai.py`) – sends the emails to a local Ollama LLM and returns a structured triage: `urgent`, `tasks`, and `digest`.
3. **Signal Notifier** (`notifier_signal.py`) – formats the triage result and delivers it via the Signal REST API.
4. **Orchestrator** (`main.py`) – wires the three modules together, reads configuration from `.env`, and handles errors gracefully.

---

## Setup

### Prerequisites

| Dependency   | Install                                                       |
| ------------ | ------------------------------------------------------------- |
| Python 3.12+ | `brew install python`                                         |
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

On first run, a browser window will open for the OAuth consent flow. The resulting token is saved automatically as `token.json` and refreshed on subsequent runs.

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

The container exposes the API on `http://localhost:8080`. Ensure it is running before each scheduled run.

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
SIGNAL_API_URL=http://localhost:8080                   # Base URL of the Signal REST API container
OLLAMA_URL=http://localhost:11434/api/generate
OLLAMA_MODEL=llama3
```

---

### 8. Test the script manually

```bash
python main.py
```

You should receive a Signal message within a few seconds. Check the terminal for any errors.

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
| `SIGNAL_API_URL`          | ❌       | `http://localhost:8080`               | Base URL of the Signal REST API container              |
| `OLLAMA_URL`              | ❌       | `http://localhost:11434/api/generate` | Ollama API endpoint                                    |
| `OLLAMA_MODEL`            | ❌       | `llama3`                              | LLM model to use for triage                            |
