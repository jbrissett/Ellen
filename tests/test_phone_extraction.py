"""Regression tests for `extract_first_phone`.

Per user direction 2026-05-18 PM: when filling qchub forms that require
a phone, pull the FIRST voice phone number from the email signature.
Email signatures commonly have several labeled phones
(`M: 804-402-9254`, `O: 857-728-5050`, `Direct: …`) — use the one
that appears first in document order. Skip fax/TTY lines explicitly;
they're not voice phones.

The fallback to `_PHONE_SENTINEL` ('000-000-0000') only kicks in when
there's no phone-pattern at all in the signature. We don't store
phones for clients in our database, so this is our only source.

Output is normalized to `XXX-XXX-XXXX` regardless of input format —
that's the common US format qchub's loose validator accepts.
"""
from __future__ import annotations

import pytest

from traffic_intake.qchub import extract_first_phone


# ----- happy paths: extract + normalize -----

@pytest.mark.parametrize("text, expected", [
    # Real signature shape from David Bernard's actual emails (Noah Smith
    # recording context). M = mobile, O = office.
    ("M: 804-402-9254\nO: 857-728-5050", "804-402-9254"),
    # Different separator styles all normalize to dashes.
    ("Phone: (555) 123-4567", "555-123-4567"),
    ("555.123.4567", "555-123-4567"),
    ("555 123 4567", "555-123-4567"),
    # Country code prefix.
    ("+1 555-123-4567", "555-123-4567"),
    ("1.555.123.4567", "555-123-4567"),
    # Extensions are silently ignored (not part of qchub field).
    ("555-123-4567 ext 89", "555-123-4567"),
    ("555-123-4567 x123", "555-123-4567"),
    # Different label vocab.
    ("Direct: 555-123-4567", "555-123-4567"),
    ("Cell: 555-123-4567", "555-123-4567"),
    ("Mobile: 555.123.4567", "555-123-4567"),
    # Bare-number-on-its-own-line still matches.
    ("Acme Corp\n123 Main St\n555-123-4567\ninfo@acme.com", "555-123-4567"),
])
def test_extract_phone_normalizes_to_dashes(text, expected):
    assert extract_first_phone(text) == expected


# ----- first-in-order wins -----

def test_first_phone_wins_when_multiple_labels():
    """The classic case: signature with Mobile + Office. Mobile is first
    in order → that's what we pick."""
    sig = (
        "Best,\n"
        "David Bernard\n"
        "Associate VP of Operations\n"
        "M: 804-402-9254\n"
        "O: 857-728-5050\n"
    )
    assert extract_first_phone(sig) == "804-402-9254"


def test_office_first_when_no_mobile():
    sig = "Jane Doe\nDirector\nO: 857-728-5050\nDirect: 555-987-6543"
    assert extract_first_phone(sig) == "857-728-5050"


# ----- fax / TTY lines are skipped -----

def test_fax_line_is_skipped():
    """`Fax:` line preceding a phone line — pick the phone line."""
    sig = "Fax: 555-111-2222\nPhone: 555-987-6543"
    assert extract_first_phone(sig) == "555-987-6543"


def test_short_f_label_skipped():
    """Abbreviated `F:` for fax also skipped."""
    sig = "F: 555-111-2222\nM: 555-987-6543"
    assert extract_first_phone(sig) == "555-987-6543"


def test_tty_skipped():
    """TTY numbers aren't voice phones."""
    sig = "TTY: 555-111-2222\nPhone: 555-987-6543"
    assert extract_first_phone(sig) == "555-987-6543"


def test_all_fax_returns_none():
    """When the only number is a fax, return None so caller falls back
    to the sentinel rather than using fax-as-phone."""
    sig = "Fax: 555-111-2222"
    assert extract_first_phone(sig) is None


# ----- edge cases / None paths -----

def test_no_phone_returns_none():
    assert extract_first_phone("Just text, no numbers") is None


def test_empty_returns_none():
    assert extract_first_phone("") is None
    assert extract_first_phone(None) is None


def test_decimal_coords_not_matched():
    """Lat/long like '27.258 -82.401' shouldn't get parsed as a phone."""
    assert extract_first_phone("Location: 27.258 -82.401") is None


def test_order_id_not_matched():
    """Numbers like 'Order #176459' or '2026-05-18' are NOT phone-shaped."""
    assert extract_first_phone("Order 176459 on 2026-05-18") is None


def test_long_digit_run_not_matched():
    """A 10-digit string with no separators shouldn't trigger a phone match
    (our regex requires separators between the three groups)."""
    assert extract_first_phone("5551234567") is None


# ----- realistic signature (Dewberry-style from WDC #5) -----

def test_realistic_full_signature_pattern():
    sig = (
        "Best regards,\n"
        "Michael Fury, P.E.\n"
        "Senior Associate | Dewberry\n"
        "8401 Arlington Blvd, Fairfax, VA 22031\n"
        "Direct: 703.849.0123\n"
        "Mobile: (703) 555-1234\n"
        "Fax: 703.849.0124\n"
        "mfury@dewberry.com\n"
        "www.dewberry.com\n"
    )
    # Direct is first → 703-849-0123 wins.
    assert extract_first_phone(sig) == "703-849-0123"


def test_inline_phone_in_body():
    """If the email body itself mentions a phone before the signature,
    that comes first. Edge case but worth pinning."""
    body = (
        "Hi David,\n"
        "please call me back at 555-123-4567 when you get a chance.\n"
        "\n"
        "Best,\n"
        "Jane\n"
        "O: 555-999-8888\n"
    )
    assert extract_first_phone(body) == "555-123-4567"
