"""Unit test for _build_office_variants — verifies that DOT-agency acronyms
in the jurisdiction string resolve to the correct QC office.

Drawn from the failure in run-20260514-093115 (Avenue Consultants / UDOT)
plus several common DOT acronyms.

Run: python tools/repro_office_variants.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from traffic_intake.models import StudyRequest  # type: ignore
from traffic_intake.qchub import _build_office_variants  # type: ignore


def make_request(jurisdiction: str) -> StudyRequest:
    return StudyRequest(
        email_subject="x",
        email_from="x@x.com",
        email_to="y@y.com",
        jurisdiction=jurisdiction,
        locations=[],
    )


def check(label, jurisdiction, expected_office_in_variants):
    variants = _build_office_variants(make_request(jurisdiction))
    ok = expected_office_in_variants in variants
    status = "PASS" if ok else "FAIL"
    print(f"  {status}  jurisdiction={jurisdiction!r:40s}  variants={variants}")
    if not ok:
        print(f"          expected '{expected_office_in_variants}' to be in variants")
    return ok


def main() -> int:
    print("DOT-acronym -> office resolution:")
    passed = 0
    total = 0
    cases = [
        # (jurisdiction string from extractor, expected office in variants)
        ("UDOT", "Salt Lake City Operations"),
        ("UDOT data collection", "Salt Lake City Operations"),
        ("FDOT", "Florida Operations"),
        ("TxDOT", "Texas Operations"),
        ("TXDOT", "Texas Operations"),
        ("GDOT", "Atlanta Operations"),
        ("TDOT", "Tennessee Operations"),
        ("WSDOT", "Portland Operations"),
        ("VDOT", "Washington DC Operations"),
        ("Caltrans", "Southern California Operations"),
        ("NCDOT", "Charlotte Operations"),
        ("PennDOT", "Washington DC Operations"),
        # Existing whole-state-name cases still work
        ("Utah", "Salt Lake City Operations"),
        ("UT", "Salt Lake City Operations"),
        ("Texas", "Texas Operations"),
        # Mixed-text cases
        ("Salt Lake County, UT", "Salt Lake City Operations"),
        ("City of Houston, TX", "Texas Operations"),
    ]
    for jurisdiction, expected in cases:
        total += 1
        if check(f"DOT", jurisdiction, expected):
            passed += 1

    # Cases that MUST NOT resolve (ambiguous DOT acronyms — we omit on purpose)
    print("\nAmbiguous DOT acronyms must NOT silently resolve:")
    ambiguous_cases = [
        # MDOT could be MI/MN/MD/ME/MS — we omit it
        ("MDOT", ["Michigan Operations", "Minnesota Operations"]),
        # ODOT could be OR/OH/OK
        ("ODOT", ["Portland Operations", "Michigan Operations"]),
        # IDOT could be IL/IA/ID
        ("IDOT", ["Chicago Operations", "Minnesota Operations",
                  "Corporate, Portland and West Coast Operations"]),
    ]
    for jurisdiction, forbidden in ambiguous_cases:
        total += 1
        variants = _build_office_variants(make_request(jurisdiction))
        # The raw jurisdiction is always included, so just check that no
        # office name slipped in from a state default.
        hit_office = next((o for o in forbidden if o in variants), None)
        ok = hit_office is None
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  jurisdiction={jurisdiction!r:10s}  variants={variants}")
        if not ok:
            print(f"          {hit_office!r} should NOT be in variants for ambiguous '{jurisdiction}'")
        if ok:
            passed += 1

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
