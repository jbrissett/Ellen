"""Unit test for Ellen's new remove_locations tool.

Synthesizes a StudyRequest with 30 locations (matching the UDOT-style scenario
that triggered this fix), runs the tool through the same dispatch path the
chat worker uses, and asserts:

- Removing a contiguous range yields the right remaining indices
- Removing out-of-order indices works (the handler sorts internally)
- Duplicate indices are deduped silently
- Out-of-range indices return an error WITHOUT mutating state
- Empty list is a no-op

Run: python tools/repro_remove_locations_tool.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from traffic_intake.chat import execute_tool  # type: ignore
from traffic_intake.models import (  # type: ignore
    StudyKind, StudyLocation, StudyRequest, TimeWindow, TubeSubtype,
)


def make_request(n: int) -> StudyRequest:
    locs = [
        StudyLocation(
            site_name=f"site-{i}",
            raw_text=f"pin {i}",
            address_or_intersection=f"x and y, city, ST",
            study_kind=StudyKind.TUBE,
            tube_subtype=TubeSubtype.VOLUME,
            time_windows=[TimeWindow(label="midweek", start="06:00", end="19:00")],
        )
        for i in range(n)
    ]
    return StudyRequest(
        email_subject="x",
        email_from="x@x.com",
        email_to="y@y.com",
        jurisdiction="UDOT",
        locations=locs,
    )


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {label}  {detail}")
    return condition


def main() -> int:
    passed = 0
    total = 0

    # ---- Case 1: the UDOT scenario — drop indices 12..25 ----
    req = make_request(30)
    total += 1
    result = execute_tool("remove_locations", {"indices": list(range(12, 26))}, req)
    expected_remaining = 30 - 14
    remaining_names = [loc.site_name for loc in req.locations]
    expected_names = [f"site-{i}" for i in list(range(0, 12)) + list(range(26, 30))]
    if check(
        "Drop indices 12..25 (the UDOT scenario)",
        len(req.locations) == expected_remaining and remaining_names == expected_names,
        f"remaining={len(req.locations)}, expected={expected_remaining}",
    ):
        passed += 1
    print(f"        tool returned: {result[:100]!r}...")

    # ---- Case 2: out-of-order indices ----
    req = make_request(10)
    total += 1
    result = execute_tool("remove_locations", {"indices": [5, 2, 7, 0]}, req)
    remaining_names = [loc.site_name for loc in req.locations]
    expected_names = ["site-1", "site-3", "site-4", "site-6", "site-8", "site-9"]
    if check(
        "Out-of-order indices [5,2,7,0] dropped correctly",
        remaining_names == expected_names,
        f"remaining={remaining_names}",
    ):
        passed += 1

    # ---- Case 3: duplicates deduped ----
    req = make_request(5)
    total += 1
    result = execute_tool("remove_locations", {"indices": [1, 1, 3, 1, 3]}, req)
    remaining_names = [loc.site_name for loc in req.locations]
    expected_names = ["site-0", "site-2", "site-4"]
    if check(
        "Duplicates [1,1,3,1,3] deduped to {1,3}",
        remaining_names == expected_names,
        f"remaining={remaining_names}",
    ):
        passed += 1

    # ---- Case 4: out-of-range returns error, doesn't mutate ----
    req = make_request(5)
    total += 1
    result = execute_tool("remove_locations", {"indices": [1, 999]}, req)
    if check(
        "Out-of-range index returns error and leaves state untouched",
        result.startswith("Error:") and len(req.locations) == 5,
        f"result={result[:80]!r}, len={len(req.locations)}",
    ):
        passed += 1

    # ---- Case 5: empty indices list is a no-op ----
    req = make_request(5)
    total += 1
    result = execute_tool("remove_locations", {"indices": []}, req)
    if check(
        "Empty indices list is a no-op",
        "No-op" in result and len(req.locations) == 5,
        f"result={result[:80]!r}",
    ):
        passed += 1

    # ---- Case 6: remove everything ----
    req = make_request(3)
    total += 1
    result = execute_tool("remove_locations", {"indices": [0, 1, 2]}, req)
    if check(
        "Removing all indices leaves zero locations",
        len(req.locations) == 0,
        f"remaining={len(req.locations)}",
    ):
        passed += 1

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
