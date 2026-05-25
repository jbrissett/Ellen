"""Verify _group_locations_for_qchub consolidates same-kind/same-time-windows
locations into a single group even when subtypes differ, picks the majority
subtype as the group default, and flags outliers for post-submit correction.

Mirrors the 2026-05-15 test case: 1 complex roundabout + 4 standard
driveways, all TMC, all AM/PM peaks. Expected: 1 group with subtype
'Turn Count -- Standard' (majority) and 1 outlier flagged (the
roundabout, which is Complex).
"""
from traffic_intake.qchub import _group_locations_for_qchub
from traffic_intake.models import (
    StudyRequest, StudyLocation, StudyKind, TMCSubtype, TimeWindow,
)


def main() -> None:
    am = TimeWindow(label="AM Peak", start="07:00", end="09:00")
    pm = TimeWindow(label="PM Peak", start="16:00", end="18:00")
    roundabout = StudyLocation(
        site_name="600 S & 250 E",
        raw_text="",
        address_or_intersection="600 S & 250 E, Smithfield, UT",
        study_kind=StudyKind.TURNING_MOVEMENT,
        tmc_subtype=TMCSubtype.COMPLEX,
        time_windows=[am, pm],
    )
    driveways = [
        StudyLocation(
            site_name=f"Driveway Access {i}",
            raw_text="",
            address_or_intersection="",
            study_kind=StudyKind.TURNING_MOVEMENT,
            tmc_subtype=TMCSubtype.STANDARD,
            time_windows=[am, pm],
        )
        for i in range(1, 5)
    ]
    req = StudyRequest(
        email_subject="t",
        email_from="",
        email_to="",
        locations=[roundabout, *driveways],
    )
    groups = _group_locations_for_qchub(req)
    print(f"{len(groups)} group(s)")
    for g in groups:
        sites = [loc.site_name for loc in g["locations"]]
        outliers = [(o["site_name"], o["actual_subtype"]) for o in g["subtype_outliers"]]
        print(f"  Group: kind={g['study_kind']}, subtype={g['subtype_label']!r}")
        print(f"    Locations ({len(sites)}): {sites}")
        print(f"    Outliers: {outliers}")
    # Expected: 1 group, subtype='Turn Count -- Standard', 5 locations, 1 outlier
    assert len(groups) == 1, f"expected 1 group, got {len(groups)}"
    g = groups[0]
    assert g["subtype_label"] == "Turn Count -- Standard", g["subtype_label"]
    assert len(g["locations"]) == 5
    assert len(g["subtype_outliers"]) == 1
    assert g["subtype_outliers"][0]["site_name"] == "600 S & 250 E"
    assert g["subtype_outliers"][0]["actual_subtype"] == "Turn Count -- Complex"
    print("\nAll assertions PASSED")


if __name__ == "__main__":
    main()
