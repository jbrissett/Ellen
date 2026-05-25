"""Unit tests for _company_matches_variants — the token-overlap matcher
that compares request firm-name variants to qchub's cascade-narrowed
*Company option.

Real-world cases drawn from observed diagnostic runs.

Run: python tools/repro_company_match.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from traffic_intake.qchub import _company_matches_variants  # type: ignore


def case(label, real_options, variants, expect):
    got = _company_matches_variants(real_options, variants)
    status = "PASS" if got is expect else "FAIL"
    print(f"  {status}  {label}")
    print(f"        options={real_options}  variants={variants}  expected={expect} got={got}")
    return got is expect


def main() -> int:
    print("Token-overlap matcher cases:")
    passed = 0
    total = 0

    # Each: (label, qchub_options, request_variants, expected_match)
    fixtures = [
        # The case that just failed in run-20260514-082737
        (
            "J-U-B parent name vs branch office label",
            ["J-U-B Engineers - Salt Lake City"],
            ["J-U-B ENGINEERS, Inc.", "jub.com", "jub"],
            True,
        ),
        # The earlier BGE case
        (
            "BGE parent name vs branch office label",
            ["BGE, Inc. - Katy"],
            ["BGE, Inc.", "bge.com", "bge"],
            True,
        ),
        # Kimley-Horn (request name is longer than qchub's option)
        (
            "Kimley-Horn long form vs short qchub label",
            ["Kimley-Horn"],
            ["Kimley-Horn and Associates, Inc.", "kimley-horn.com", "kimley-horn"],
            True,
        ),
        # Exact match
        (
            "Exact match",
            ["Acme Engineers"],
            ["Acme Engineers"],
            True,
        ),
        # The false-positive guard: two different firms sharing one generic word
        (
            "Different firms sharing only 'Engineers' must NOT match",
            ["ABC Engineers"],
            ["XYZ Engineers, Inc.", "xyz.com", "xyz"],
            False,
        ),
        # Different firms entirely
        (
            "Totally unrelated firms must NOT match",
            ["Foothills Consulting"],
            ["BGE, Inc.", "bge.com", "bge"],
            False,
        ),
        # Empty qchub options
        (
            "Empty options list short-circuits to False",
            [],
            ["BGE, Inc."],
            False,
        ),
        # Empty variants
        (
            "Empty variants list short-circuits to False",
            ["BGE, Inc. - Katy"],
            [],
            False,
        ),
        # Suffix-only difference
        (
            "Same firm with different corporate suffix",
            ["Acme Engineering LLC"],
            ["Acme Engineering, Inc.", "acme.com", "acme"],
            True,
        ),
    ]

    for label, opts, variants, expect in fixtures:
        total += 1
        if case(label, opts, variants, expect):
            passed += 1

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
