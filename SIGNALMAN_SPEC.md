# Spec: "Signalman" (MVP)

## 1. Project Goal

A modular Python automation running on a Mac Mini that triages Gmail via a local LLM (Ollama) and delivers a structured daily briefing via Signal.

## 2. Technical Stack

- Language: Python 3.12+
- Testing: pytest (Mandatory test-first approach)
- Email: Google API Client (Gmail)
- AI: Ollama (Local API)
- Messaging: Signal REST API (Docker container: `bbernhard/signal-cli-rest-api`)

## 3. Modular Architecture (For Future Growth)

- `provider_gmail.py`: Handles authentication and fetching raw email data.
- `processor_ai.py`: Handles the prompt engineering and Ollama API calls.
- `notifier_signal.py`: A clean interface to send strings to Signal.
- `main.py`: The orchestrator that ties the modules together.

## 4. MVP Functional Requirements

1. Fetch: Retrieve unread emails from the last 24 hours.
2. Clean: Strip HTML and signatures to save LLM tokens.
3. Triage: Use a local LLM to return a JSON-structured summary:
   - `urgent`: Items requiring a reply today.
   - `tasks`: Action items identified in text.
   - `digest`: Brief summaries of lower-priority info.
4. Send: Format the JSON into a clean, human-readable Signal message.

## 5. Test-First Requirements

The project must include a `tests/` directory with:

- **Mocked Gmail Tests**: Ensure the fetcher handles empty inboxes and malformed emails.
- **Mocked AI Tests**: Ensure the processor correctly handles various LLM response formats.
- **Signal Interface Tests**: Ensure the HTTP payload is correctly constructed and the API endpoint is called.
