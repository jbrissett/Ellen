"""Unit test for set_location_time_windows_by_kind.

The bug we're preventing: in order 176400 (2026-05-14), Ellen added 35
locations meaning to have 7 TMCs + 28 tube approaches but only 6 made it
in as TMCs. She then said 'indices 0-6 for 7 TMCs' to the index-based
time-windows tool; index 6 was actually the first tube, so it got the
TMC's 12-hr window. Net: 1 mis-classified location, 1 phantom group in
qchub, and one TMC group with the wrong site count.

The by-kind tool can't make this mistake — it filters by study_kind, so
'all TMCs get the 12-hr window' is invariant under add ordering.

Run: python tools/repro_set_time_windows_by_kind.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from traffic_intake.chat import execute_tool  # type: ignore
from traffic_intake.models import (  # type: ignore
    LocationEstimate, StudyKind, StudyLocation, StudyRequest, SurveySubtype,
    TimeWindow, TMCSubtype, TubeSubtype,
)


def make_loc(name, kind, subtype) -> StudyLocation:
    loc = StudyLocation(
        site_name=name,
        raw_text=name,
        address_or_intersection=name,
        study_kind=kind,
        time_windows=[],  # start empty — tool will fill
        estimate=LocationEstimate(latitude=27.3, longitude=-82.4, confidence="medium", source="geocoded"),
    )
    if kind == StudyKind.TURNING_MOVEMENT:
        loc.tmc_subtype = TMCSubtype(subtype)
    elif kind == StudyKind.TUBE:
        loc.tube_subtype = TubeSubtype(subtype)
    elif kind == StudyKind.SURVEY:
        loc.survey_subtype = SurveySubtype(subtype)
    return loc


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    safe = lambda s: s.encode("ascii", "replace").decode("ascii") if isinstance(s, str) else s
    print(f"  {status}  {safe(label)}  {safe(detail)}")
    return condition


def main() -> int:
    passed = 0
    total = 0

    # ---- Case 1: clean FDOT-like mix (6 TMCs + 28 tubes + 1 survey) ----
    locs = (
        [make_loc(f"TMC {i}", StudyKind.TURNING_MOVEMENT, "standard") for i in range(6)]
        + [make_loc(f"Tube {i}", StudyKind.TUBE, "volume") for i in range(28)]
        + [make_loc("Gap", StudyKind.SURVEY, "vehicular_gap_study")]
    )
    req = StudyRequest(
        email_subject="x", email_from="x@x", email_to="y@y", locations=locs,
    )
    total += 1
    result = execute_tool("set_location_time_windows_by_kind", {
        "study_kind": "turning_movement",
        "windows": [{"label": "12-hr", "start": "07:00", "end": "19:00"}],
    }, req)
    tmc_with_window = sum(1 for loc in req.locations if loc.study_kind == StudyKind.TURNING_MOVEMENT and loc.time_windows)
    tube_with_window = sum(1 for loc in req.locations if loc.study_kind == StudyKind.TUBE and loc.time_windows)
    if check(
        "Set 12-hr on all TMCs: 6 TMCs touched, 0 tubes touched",
        tmc_with_window == 6 and tube_with_window == 0,
        f"TMC touched={tmc_with_window}, Tube touched={tube_with_window}",
    ):
        passed += 1

    # ---- Case 2: now set tubes — TMCs should remain at their 12-hr window ----
    total += 1
    execute_tool("set_location_time_windows_by_kind", {
        "study_kind": "tube",
        "windows": [{"label": "72-hr", "start": "00:00", "end": "23:59", "total_hours": 72}],
    }, req)
    tmc_still_12hr = all(
        loc.time_windows and loc.time_windows[0].start == "07:00"
        for loc in req.locations if loc.study_kind == StudyKind.TURNING_MOVEMENT
    )
    tube_at_72hr = all(
        loc.time_windows and loc.time_windows[0].total_hours == 72
        for loc in req.locations if loc.study_kind == StudyKind.TUBE
    )
    if check(
        "Setting tubes doesn't disturb TMCs; tubes get 72-hr total_hours",
        tmc_still_12hr and tube_at_72hr,
        "",
    ):
        passed += 1

    # ---- Case 3: the FDOT 176400 off-by-one scenario, with index-based tool ----
    # Reproduce the exact failure: 6 TMCs + 28 tubes + 1 midblock = 35 sites.
    # Agent erroneously calls set_location_time_windows_for_indices on 0-6,
    # which hits 6 TMCs + 1 tube. The tube gets stamped with the TMC's window.
    locs2 = (
        [make_loc(f"TMC {i}", StudyKind.TURNING_MOVEMENT, "standard") for i in range(6)]
        + [make_loc(f"Tube {i}", StudyKind.TUBE, "volume") for i in range(28)]
        + [make_loc("Mid", StudyKind.TUBE, "volume_class")]
    )
    req2 = StudyRequest(
        email_subject="x", email_from="x@x", email_to="y@y", locations=locs2,
    )
    # The OLD (index-based) call — proves the bug.
    execute_tool("set_location_time_windows_for_indices", {
        "indices": list(range(7)),  # the off-by-one
        "windows": [{"label": "12-hr", "start": "07:00", "end": "19:00"}],
    }, req2)
    mis_stamped_tubes = sum(
        1 for loc in req2.locations
        if loc.study_kind == StudyKind.TUBE
        and loc.time_windows
        and loc.time_windows[0].start == "07:00"
    )
    total += 1
    if check(
        "INDEX-BASED tool bug repro: 1 tube mis-stamped with TMC's 12-hr window (the 176400 failure)",
        mis_stamped_tubes == 1,
        f"mis-stamped={mis_stamped_tubes}",
    ):
        passed += 1

    # ---- Case 4: same scenario with BY-KIND tool — bug cannot occur ----
    locs3 = (
        [make_loc(f"TMC {i}", StudyKind.TURNING_MOVEMENT, "standard") for i in range(6)]
        + [make_loc(f"Tube {i}", StudyKind.TUBE, "volume") for i in range(28)]
        + [make_loc("Mid", StudyKind.TUBE, "volume_class")]
    )
    req3 = StudyRequest(
        email_subject="x", email_from="x@x", email_to="y@y", locations=locs3,
    )
    execute_tool("set_location_time_windows_by_kind", {
        "study_kind": "turning_movement",
        "windows": [{"label": "12-hr", "start": "07:00", "end": "19:00"}],
    }, req3)
    mis_stamped_tubes = sum(
        1 for loc in req3.locations
        if loc.study_kind == StudyKind.TUBE
        and loc.time_windows
        and loc.time_windows[0].start == "07:00"
    )
    total += 1
    if check(
        "BY-KIND tool prevents the 176400 bug: ZERO tubes touched when TMC windows are set",
        mis_stamped_tubes == 0,
        f"mis-stamped={mis_stamped_tubes}",
    ):
        passed += 1

    # ---- Case 5: empty match returns clear no-op ----
    req4 = StudyRequest(
        email_subject="x", email_from="x@x", email_to="y@y",
        locations=[make_loc("Only TMC", StudyKind.TURNING_MOVEMENT, "standard")],
    )
    total += 1
    result = execute_tool("set_location_time_windows_by_kind", {
        "study_kind": "survey",
        "windows": [{"label": "x", "start": "00:00", "end": "23:59"}],
    }, req4)
    if check(
        "No locations of the target kind -> clear no-op message",
        "No-op" in result and "survey" in result,
        f"result={result[:100]!r}",
    ):
        passed += 1

    # ---- Case 6: TMC subtype is preserved (kind-filter doesn't disturb subtype) ----
    locs5 = [
        make_loc("Std", StudyKind.TURNING_MOVEMENT, "standard"),
        make_loc("Lg", StudyKind.TURNING_MOVEMENT, "large"),
        make_loc("Cmplx", StudyKind.TURNING_MOVEMENT, "complex"),
    ]
    req5 = StudyRequest(
        email_subject="x", email_from="x@x", email_to="y@y", locations=locs5,
    )
    execute_tool("set_location_time_windows_by_kind", {
        "study_kind": "turning_movement",
        "windows": [{"label": "x", "start": "07:00", "end": "19:00"}],
    }, req5)
    total += 1
    if check(
        "Subtypes preserved across all 3 TMCs after by-kind window set",
        req5.locations[0].tmc_subtype == TMCSubtype.STANDARD
        and req5.locations[1].tmc_subtype == TMCSubtype.LARGE
        and req5.locations[2].tmc_subtype == TMCSubtype.COMPLEX,
        "",
    ):
        passed += 1

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
