"""Email body cleaner for Signalman.

Pure functions, no I/O. Strips the dead weight that makes an LLM slower and
less accurate — quoted reply history, signatures and marketing footers —
then bounds the length.

The one rule that matters: **never return an empty string for a non-empty
body.** Every pattern here is a heuristic, and a false positive must degrade
to noisy input, never to no input — a wrongly-empty body silently removes an
email from consideration.
"""

from __future__ import annotations

import re

TRUNCATION_MARKER = "… [truncated]"

# A footer block longer than this is assumed to be real content that merely
# mentions e.g. "privacy policy".
_MAX_FOOTER_BLOCK_CHARS = 600

# Patterns that mark the start of quoted history. Everything from the
# earliest match onwards is discarded.
_QUOTE_PATTERNS = [
    # "On Mon, 5 Oct 2026 at 09:00, Bob <bob@x.com> wrote:" — Gmail sometimes
    # wraps "wrote:" onto the next line, so allow one line break.
    re.compile(r"^On (?:[^\n]*\n)?[^\n]*\bwrote:[ \t]*$", re.MULTILINE),
    re.compile(r"^-{3,}\s*Original Message\s*-{3,}[ \t]*$", re.MULTILINE | re.IGNORECASE),
    # Outlook's underscore rule.
    re.compile(r"^_{10,}[ \t]*$", re.MULTILINE),
    # Outlook header block: "From: …" followed within 3 lines by "Sent: …".
    re.compile(r"^From: .+\n(?:[^\n]*\n){0,2}?Sent: ", re.MULTILINE),
    # Four or more consecutive ">" lines.
    re.compile(r"^>[^\n]*(?:\n>[^\n]*){3,}", re.MULTILINE),
]

# Patterns that mark the start of a signature. Everything from the earliest
# match onwards is discarded.
_SIGNATURE_PATTERNS = [
    # RFC 3676 delimiter ("-- " with its trailing space usually stripped).
    re.compile(r"^--[ \t]*$", re.MULTILINE),
    re.compile(r"^(?:Sent from my\b|Get Outlook for\b)", re.MULTILINE | re.IGNORECASE),
]

# A trailing block matching any of these is footer boilerplate.
_FOOTER_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bunsubscribe\b",
        r"\bmanage (?:your )?(?:email )?(?:preferences|subscriptions?)\b",
        r"\bview (?:this (?:email|message) )?(?:in|online|on the web)\b",
        r"\bopen in (?:your )?browser\b",
        r"\b(?:you are|you're) receiving this\b",
        r"\byou (?:received|got) this (?:email|message)\b",
        r"\bthis (?:email|message) was sent to\b",
        r"\bprivacy policy\b",
        r"\ball rights reserved\b",
        r"©|\(c\)\s*\d{4}",
        r"\bP\.?O\.? Box \d+",
        # Street address: "123 Example Street", "10 Downing St, London"
        # (case-sensitive, or "5 on my way" would look like an address)
        r"(?-i:\b\d{1,5}\s+(?:[A-Z][\w.'-]*\s+){1,4}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Way|Suite|Ste)\b)",
        # UK postcode
        r"(?-i:\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b)",
    )
]


def clean(body: str, *, max_chars: int) -> str:
    """Return *body* with reply history, signature and footer removed.

    The result is whitespace-normalised and no longer than *max_chars*
    (truncated on a word boundary with a trailing ``… [truncated]``).

    If the heuristics would strip everything, the whitespace-normalised
    original is returned (truncated) instead. An empty or whitespace-only
    *body* returns ``""``.

    Raises:
        ValueError: If *max_chars* is less than 1.
    """
    if max_chars < 1:
        raise ValueError(f"max_chars must be at least 1, got {max_chars}")

    original = _normalise_whitespace(body)
    if not original:
        return ""

    text = _cut_at_first_match(original, _QUOTE_PATTERNS)
    text = _cut_at_first_match(text, _SIGNATURE_PATTERNS)
    text = _normalise_whitespace(_drop_trailing_footer_blocks(text))

    return _truncate(text or original, max_chars)


def _normalise_whitespace(text: str) -> str:
    """Unify line endings, strip trailing spaces, collapse 3+ newlines to 2."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _cut_at_first_match(text: str, patterns: list[re.Pattern[str]]) -> str:
    """Discard everything from the earliest match of any pattern onwards."""
    cut = len(text)
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            cut = min(cut, match.start())
    return text[:cut].rstrip()


def _drop_trailing_footer_blocks(text: str) -> str:
    """Remove footer boilerplate from the end of *text*.

    Works block by block (blank-line separated) from the end. A block that
    looks like a footer is dropped whole — unless content precedes the first
    footer-looking line in it (html2text emits "Pay now" and the footer on
    adjacent lines), in which case only the footer lines are cut and the
    walk stops.
    """
    blocks = re.split(r"\n\s*\n", text)
    while blocks:
        block = blocks[-1]
        if len(block) > _MAX_FOOTER_BLOCK_CHARS:
            break
        lines = block.split("\n")
        first_footer = next((i for i, line in enumerate(lines) if _is_footer_line(line)), None)
        if first_footer is None:
            break
        if first_footer > 0:
            blocks[-1] = "\n".join(lines[:first_footer])
            break
        blocks.pop()
    return "\n\n".join(blocks)


def _is_footer_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in _FOOTER_PATTERNS)


def _truncate(text: str, max_chars: int) -> str:
    """Truncate to at most *max_chars* on a word boundary, marking the cut."""
    if len(text) <= max_chars:
        return text
    if max_chars <= len(TRUNCATION_MARKER):
        return text[:max_chars]

    room = max_chars - len(TRUNCATION_MARKER)
    head = text[:room]
    # Back up to the last whitespace unless that would throw away most of the text.
    boundary = max(head.rfind(" "), head.rfind("\n"), head.rfind("\t"))
    if boundary >= room // 2:
        head = head[:boundary]
    return head.rstrip() + TRUNCATION_MARKER
