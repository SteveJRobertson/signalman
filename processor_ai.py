"""AI processor for Signalman.

Sends email snippets to a local Ollama LLM for triage and returns a
structured JSON summary with three categories:

- ``urgent``: Items requiring a reply today.
- ``tasks``:  Action items identified in the email text.
- ``digest``: Brief summaries of lower-priority information.
"""

from __future__ import annotations

import json
import textwrap
from typing import Any

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "llama3"


def _normalise_list(value: Any) -> list[str]:
    """Coerce an LLM-returned field to a flat list of strings.

    Handles the non-deterministic output shapes an LLM may return:
    - list/tuple  → each element coerced to str
    - bare string → wrapped in a one-element list (avoids per-char iteration)
    - None        → empty list
    - anything else (int, dict, …) → wrapped as a single str
    """
    if isinstance(value, (list, tuple)):
        return [str(i) for i in value]
    if value is None:
        return []
    return [str(value)]


_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an email triage assistant. You will be given a list of emails.
    Your job is to classify each email into exactly one of three categories:

    - urgent: Emails that require an immediate response today. This includes anything related to
              job applications (interview invites, offer deadlines, recruiter follow-ups),
              children's school communications as a parent (e.g. paying for milk or school lunches,
              upcoming assemblies, school trips, packed lunch reminders, permission slips),
              and any email that explicitly asks for a reply or action by end of day.
    - tasks:  Emails that contain clear action items but are not time-critical today
              (e.g. follow-ups, requests with a future deadline, things to schedule).
    - digest: Low-priority emails such as newsletters, FYIs, marketing, or general
              information that need no immediate action.

    Ignore junk, spam, or automated notifications that require no action.

    Respond with ONLY valid JSON in the following format, with no additional text:
    {
      "urgent": ["<concise description>", ...],
      "tasks":  ["<concise description>", ...],
      "digest": ["<concise description>", ...]
    }
""")


class AIProcessor:
    """Triages a list of emails using a local Ollama LLM."""

    def __init__(
        self,
        url: str = OLLAMA_URL,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.url = url
        self.model = model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def triage(self, emails: list[dict]) -> dict[str, list[str]]:
        """Send *emails* to the LLM and return a triage dict.

        Args:
            emails: A list of email dicts with keys ``id``, ``subject``,
                    ``sender``, and ``body`` (as returned by
                    :class:`~provider_gmail.GmailProvider`).

        Returns:
            A dict with keys ``urgent``, ``tasks``, and ``digest``, each
            mapping to a list of concise string descriptions.

        Raises:
            requests.HTTPError: If the Ollama API returns a non-2xx status.
            ValueError: If the model response cannot be parsed as JSON.
        """
        if not emails:
            return {"urgent": [], "tasks": [], "digest": []}

        prompt = self._build_prompt(emails)
        raw_response = self._call_ollama(prompt)
        return self._parse_response(raw_response)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_prompt(self, emails: list[dict]) -> str:
        """Construct the full prompt from the system instructions and emails.

        Security note: email content (subject, sender, body) is interpolated
        directly into the prompt and is not sanitised. A malicious email could
        attempt prompt injection. This is a known, accepted risk for a local
        personal-use tool. The impact is limited to the triage output because
        _parse_response() coerces all values to strings.
        """
        lines = [_SYSTEM_PROMPT, "Here are the emails to triage:\n"]
        for i, email in enumerate(emails, start=1):
            lines.append(f"--- Email {i} ---")
            lines.append(f"From: {email.get('sender', '')}")
            lines.append(f"Subject: {email.get('subject', '')}")
            lines.append(f"Body: {email.get('body', '')}")
            lines.append("")
        return "\n".join(lines)

    def _call_ollama(self, prompt: str) -> str:
        """POST the prompt to the Ollama API and return the raw response text."""
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        response = requests.post(self.url, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()["response"]

    @staticmethod
    def _parse_response(raw: str) -> dict[str, list[str]]:
        """Parse the LLM's JSON response into the triage dict.

        Raises:
            ValueError: If *raw* is not valid JSON or is missing expected keys.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse LLM response as JSON: {exc}\nRaw: {raw}") from exc

        return {
            "urgent": _normalise_list(data.get("urgent")),
            "tasks": _normalise_list(data.get("tasks")),
            "digest": _normalise_list(data.get("digest")),
        }
