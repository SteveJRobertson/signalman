"""Tests for the email body cleaner.

Pure functions, so no mocking. The fixtures are realistic reply chains,
signatures and footers; the safety tests (a body that is only a quote must
not empty out) matter more than the happy paths, because a wrongly-empty
body silently drops an email from consideration.
"""

from __future__ import annotations

import pytest

from cleaner import TRUNCATION_MARKER, clean

BIG = 10_000

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GMAIL_CHAIN = """\
Yes, Thursday at 3pm works for me.

On Mon, 5 Oct 2026 at 09:12, Alice Example <alice@example.com> wrote:
> Can we meet this week?
> I'm free Thursday or Friday.
> Let me know.
> Thanks, Alice
"""

GMAIL_CHAIN_WRAPPED = """\
Yes, Thursday at 3pm works for me.

On Mon, 5 Oct 2026 at 09:12, Alice Example <alice.with.a.very.long.address@example.com>
wrote:
> Can we meet this week?
"""

OUTLOOK_CHAIN = """\
Approved, please go ahead.

From: Bob Builder <bob@example.com>
Sent: 05 October 2026 09:12
To: Steve
Subject: Quote for sign-off

Hi Steve, the quote is attached. Total is £4,200.
"""

OUTLOOK_UNDERSCORE_CHAIN = """\
Approved, please go ahead.

________________________________
From: Bob Builder
Sent: 05 October 2026 09:12
Hi Steve, the quote is attached.
"""

ORIGINAL_MESSAGE_CHAIN = """\
Sounds good.

-----Original Message-----
From: Carol
Sent: Monday
Shall we go ahead?
"""

BARE_QUOTE_CHAIN = """\
Fine by me.

> older line one
> older line two
> older line three
> older line four
"""

DASH_SIGNATURE = """\
Please send the invoice by Friday.

--
Dave Smith
Head of Things, Example Ltd
+44 1234 567890
"""

DASH_SIGNATURE_TRAILING_SPACE = "Please send the invoice by Friday.\n\n-- \nDave Smith\n"

MOBILE_SIGNATURE = """\
Running late, start without me.

Sent from my iPhone
"""

OUTLOOK_MOBILE_SIGNATURE = "Running late, start without me.\n\nGet Outlook for iOS\n"

NEWSLETTER = """\
# Your October statement is ready

Balance due: £42.00 by 15 November.

Pay now

You are receiving this email because you signed up at example.com.

Unsubscribe | Manage preferences | View in browser

Example Ltd, 10 Downing Street, London SW1A 2AA

© 2026 Example Ltd. All rights reserved.
"""


# ---------------------------------------------------------------------------
# Table-driven: what survives, what goes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "body", "kept", "removed"),
    [
        ("gmail chain", GMAIL_CHAIN, ["Thursday at 3pm"], ["Can we meet", "Alice", "wrote:"]),
        (
            "gmail chain, wrapped header",
            GMAIL_CHAIN_WRAPPED,
            ["Thursday at 3pm"],
            ["Can we meet", "wrote:"],
        ),
        ("outlook chain", OUTLOOK_CHAIN, ["Approved"], ["Bob Builder", "£4,200", "Sent:"]),
        (
            "outlook underscore rule",
            OUTLOOK_UNDERSCORE_CHAIN,
            ["Approved"],
            ["quote is attached", "Bob"],
        ),
        ("original message marker", ORIGINAL_MESSAGE_CHAIN, ["Sounds good"], ["Shall we", "Carol"]),
        ("bare > quote block", BARE_QUOTE_CHAIN, ["Fine by me"], ["older line"]),
        ("-- signature", DASH_SIGNATURE, ["invoice by Friday"], ["Dave Smith", "567890"]),
        (
            "-- signature with trailing space",
            DASH_SIGNATURE_TRAILING_SPACE,
            ["invoice by Friday"],
            ["Dave Smith"],
        ),
        ("mobile signature", MOBILE_SIGNATURE, ["start without me"], ["iPhone"]),
        ("outlook mobile signature", OUTLOOK_MOBILE_SIGNATURE, ["start without me"], ["Outlook"]),
        (
            "newsletter footer",
            NEWSLETTER,
            ["October statement", "42.00", "Pay now"],
            ["Unsubscribe", "receiving this", "Downing Street", "All rights reserved"],
        ),
    ],
)
def test_removes_clutter_and_keeps_content(name, body, kept, removed):
    result = clean(body, max_chars=BIG)

    for text in kept:
        assert text in result, f"{name}: lost {text!r}"
    for text in removed:
        assert text not in result, f"{name}: still contains {text!r}"


def test_long_reply_chain_keeps_only_the_newest_message():
    chain = "Newest reply.\n\n" + "".join(
        f"On Day {i}, Person {i} <p{i}@x.com> wrote:\n> older message {i}\n\n" for i in range(10)
    )

    assert clean(chain, max_chars=BIG) == "Newest reply."


def test_earliest_cut_wins_when_several_markers_present():
    body = "Top.\n\n--\nSig\n\nOn Mon, X wrote:\n> old\n"

    assert clean(body, max_chars=BIG) == "Top."


def test_footer_on_adjacent_lines_keeps_the_content_above_it():
    """html2text puts footer lines directly under the last content line."""
    body = (
        "Balance due: £42.00.\n\nPay now\n"
        "You are receiving this email because you subscribed.\n"
        "Unsubscribe | Privacy Policy\n"
        "Acme Ltd, 10 Downing Street, London SW1A 2AA"
    )

    assert clean(body, max_chars=BIG) == "Balance due: £42.00.\n\nPay now"


def test_wrapped_footer_paragraph_is_dropped_whole():
    body = "Real content.\n\nYou are receiving this email because\nyou signed up at example.com."

    assert clean(body, max_chars=BIG) == "Real content."


def test_footer_mention_inside_content_block_cuts_only_from_that_line():
    body = "Thanks for the update.\nPlease unsubscribe me from the list."

    assert clean(body, max_chars=BIG) == "Thanks for the update."


# ---------------------------------------------------------------------------
# Safety: never return empty
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        "> only\n> a\n> quoted\n> block\n> here",
        "On Mon, 5 Oct 2026, Alice <a@x.com> wrote:\n> everything is a quote",
        "-----Original Message-----\nFrom: Carol\nSent: Monday\nForwarded text",
        "--\nSignature only",
        "Sent from my iPhone",
        "Unsubscribe | Manage preferences",
        "© 2026 Example Ltd. All rights reserved.",
    ],
)
def test_body_that_is_entirely_clutter_does_not_empty_out(body):
    result = clean(body, max_chars=BIG)

    assert result != ""
    assert result == body.strip()


def test_fallback_is_still_truncated():
    body = "> " + "quoted words " * 200
    body = "\n".join([body] * 4)

    result = clean(body, max_chars=100)

    assert 0 < len(result) <= 100
    assert result.endswith(TRUNCATION_MARKER)


@pytest.mark.parametrize("body", ["", "   ", "\n\n\t\n"])
def test_empty_or_blank_input_returns_empty_string(body):
    assert clean(body, max_chars=BIG) == ""


# ---------------------------------------------------------------------------
# Pass-through and false-positive resistance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "body",
    [
        "Can you call me when you land?",
        "Hi Steve,\n\nThe boiler service is booked for Tuesday.\n\nThanks,\nSam",
        "Line one.\n\nLine two.\n\nLine three.",
        # Mentions footer words in a real, long paragraph — must survive.
        ("Please read the attached privacy policy carefully and tell me whether you are happy "
         "with section 4 before we sign. " * 8).strip(),
        # Hyphens and markdown rules are not signature delimiters.
        "Intro.\n\n---\n\nAfter the rule.",
        "Intro.\n\n---- not a delimiter ----\n\nMore.",
        # Looks vaguely like an address but is prose.
        "Intro paragraph.\n\nCall me at 5 on my way home",
        # A single '>' line is not a quote block.
        "He wrote:\n> just one quoted line\nand then my reply.",
    ],
)
def test_clean_body_passes_through_unchanged(body):
    assert clean(body, max_chars=BIG) == body


# ---------------------------------------------------------------------------
# Whitespace
# ---------------------------------------------------------------------------

def test_collapses_blank_runs_strips_trailing_spaces_and_normalises_newlines():
    body = "Hello   \r\n\r\n\r\n\r\n\r\nWorld \t\r\n"

    assert clean(body, max_chars=BIG) == "Hello\n\nWorld"


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

class TestTruncation:
    def test_short_body_is_not_truncated(self):
        assert clean("short body", max_chars=100) == "short body"

    def test_body_exactly_at_limit_is_not_truncated(self):
        body = "x" * 50

        assert clean(body, max_chars=50) == body

    def test_long_body_is_truncated_on_a_word_boundary_with_marker(self):
        body = "alpha bravo charlie delta echo foxtrot golf hotel india juliet " * 3

        result = clean(body, max_chars=60)

        assert len(result) <= 60
        assert result.endswith(TRUNCATION_MARKER)
        kept = result[: -len(TRUNCATION_MARKER)].split()
        assert kept, "expected some text before the marker"
        vocabulary = set(body.split())
        assert all(word in vocabulary for word in kept), "cut a word in half"

    def test_unbroken_text_is_hard_cut(self):
        result = clean("x" * 500, max_chars=100)

        assert len(result) == 100
        assert result.endswith(TRUNCATION_MARKER)

    def test_limit_smaller_than_marker_hard_cuts_without_marker(self):
        assert clean("abcdefghij" * 10, max_chars=5) == "abcde"

    def test_truncation_happens_after_cleaning(self):
        """Quoted history must not eat the character budget."""
        body = "Real content here.\n\nOn Mon, X wrote:\n" + "> noise\n" * 500

        assert clean(body, max_chars=100) == "Real content here."

    @pytest.mark.parametrize("bad", [0, -1])
    def test_max_chars_below_one_rejected(self, bad):
        with pytest.raises(ValueError, match="max_chars"):
            clean("body", max_chars=bad)
