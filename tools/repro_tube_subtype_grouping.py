"""Verify the FDOT D1 case: 7 TMCs + 28 Volume tube approaches + 1 Volume,Class
mid-block, all sharing time-window scopes, now split into 3 groups by the
updated planner (was 2 groups before).

Before 2026-05-17: 2 groups
    Group 1: 7 TMCs       (Midweek 7:00-19:00, 12hr)
    Group 2: 29 Tubes     (72hr) — majority subtype "Volume"; the 1 mid-block
                                   Volume,Class fell into subtype_outliers
                                   (which Ellen then silently failed to fix
                                    post-submit in test order 176456).

After:        3 groups
    Group 1: 7 TMCs       (Midweek 7:00-19:00, 12hr)
    Group 2: 28 Tubes     (72hr, Volume)
    Group 3: 1 Tube       (72hr, Volume,Class) — its own deliverable
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from traffic_intake.models import (  # noqa: E402
    LocationEstimate,
    StudyKind,
    StudyLocation,
    StudyRequest,
    TimeWindow,
    TMCSubtype,
    TubeSubtype,
)
from traffic_intake.qchub import _group_locations_for_qchub  # noqa: E402


def _loc(name: str, kind: StudyKind, tube_sub=None, tmc_sub=None, tws=None) -> StudyLocation:
    return StudyLocation(
        site_name=name,
        raw_text=name,
        address_or_intersection=f"{name}, Sarasota, FL",
        study_kind=kind,
        tube_subtype=tube_sub,
        tmc_subtype=tmc_sub,
        time_windows=tws or [],
        estimate=LocationEstimate(
            latitude=27.27, longitude=-82.40, confidence="high", source="kmz"
        ),
    )


tmc_window = [TimeWindow(label="Midweek", start="07:00", end="19:00")]
tube_window = [TimeWindow(label="Midweek", start="00:00", end="23:59", total_hours=72)]

# 7 TMCs
locations = [
    _loc(f"SR 72 -- {name}", StudyKind.TURNING_MOVEMENT,
         tmc_sub=TMCSubtype.STANDARD, tws=tmc_window)
    for name in ["Lorraine", "Proctor", "Preservation", "Aventura",
                 "Churchill Downs", "Coash", "Timberland"]
]

# 28 Volume tube approaches
locations += [
    _loc(f"SR 72 & {inter} -- {leg} approach", StudyKind.TUBE,
         tube_sub=TubeSubtype.VOLUME, tws=tube_window)
    for inter in ["Proctor", "Preservation", "Aventura",
                  "Churchill Downs", "Coash", "Timberland", "Lorraine"]
    for leg in ["N", "S", "E", "W"]
]

# 1 mid-block Volume,Class tube
locations.append(
    _loc("SR 72 Mid-block (Proctor to Lorraine)", StudyKind.TUBE,
         tube_sub=TubeSubtype.VOLUME_CLASS, tws=tube_window)
)

req = StudyRequest(
    email_subject="FDOT D1 — SR 72",
    email_from="andrew@kimley-horn.com",
    email_to="data-entry@qualitycounts.net",
    locations=locations,
)

groups = _group_locations_for_qchub(req)

print(f"=== Planner produced {len(groups)} group(s) ===\n")
for i, g in enumerate(groups, 1):
    n = len(g["locations"])
    print(f"Group {i}: kind={g['study_kind']:<16} subtype={g['subtype_label']!s:<25} "
          f"window={g['time_windows'][0].start}-{g['time_windows'][0].end} "
          f"({n} loc{'s' if n != 1 else ''}) outliers={len(g['subtype_outliers'])}")

# Expectations
assert len(groups) == 3, f"Expected 3 groups, got {len(groups)}"

by_kind = {(g["study_kind"], g["subtype_label"]): len(g["locations"]) for g in groups}
expected = {
    ("turning_movement", "Turn Count -- Standard"): 7,     # All 7 TMCs together
    ("tube", "Volume"): 28,                                # 28 Volume approach tubes
    ("tube", "Volume, Class"): 1,                          # mid-block Volume,Class on its own
}

# Note: subtype_label values come from _qchub_subtype_label — we just check
# the counts match the right (kind, label) pair.
for key, want in expected.items():
    got = by_kind.get(key)
    if got != want:
        print(f"\nFAIL: expected {want} locations in group ({key}), got {got}")
        print(f"      actual groupings: {by_kind}")
        raise SystemExit(1)

print("\nPASS: Volume and Volume,Class tubes are now separate groups; "
      "TMCs stayed bundled.")
