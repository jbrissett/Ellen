"""Unit test for build_kml_for_locations + _group_layer_name + per-group flow shape.

Verifies the foundation of John Goodwin's workflow change (2026-05-14):
- kml_export.build_kml_for_locations(subset) emits KML with ONLY those locations
- The <name> tag in the KML reflects the layer name (so MyMaps surfaces it
  as the layer label when imported)
- _group_layer_name produces sensible labels for TMC / Tube / Survey groups
- Number of placemarks in the KML matches the subset (not the whole request)

Run: python tools/repro_per_group_kml.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from traffic_intake.kml_export import build_kml, build_kml_for_locations  # type: ignore
from traffic_intake.models import (  # type: ignore
    LocationEstimate, StudyKind, StudyLocation, StudyRequest, SurveySubtype,
    TimeWindow, TMCSubtype, TubeSubtype,
)
from traffic_intake.qchub import _group_layer_name, _group_locations_for_qchub  # type: ignore


def make_loc(name, kind, subtype, lat=27.3, lon=-82.4) -> StudyLocation:
    loc = StudyLocation(
        site_name=name,
        raw_text=name,
        address_or_intersection=name,
        study_kind=kind,
        time_windows=[TimeWindow(label="7am-7pm", start="07:00", end="19:00")],
        estimate=LocationEstimate(latitude=lat, longitude=lon, confidence="medium", source="geocoded"),
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

    # ---- Build the FDOT scope: 7 TMCs + 1 mid-block Volume,Class tube + 28 approach Volume tubes ----
    tmcs = [make_loc(f"TMC {i}", StudyKind.TURNING_MOVEMENT, "standard") for i in range(7)]
    midblock = [make_loc("Mid-block VC", StudyKind.TUBE, "volume_class")]
    approaches = [make_loc(f"Approach {i}", StudyKind.TUBE, "volume") for i in range(28)]
    # Approach tubes need total_hours=72 to be in a different group than midblock
    for a in approaches:
        a.time_windows = [TimeWindow(label="72h", start="00:00", end="23:59", total_hours=72)]
    midblock[0].time_windows = [TimeWindow(label="72h", start="00:00", end="23:59", total_hours=72)]

    req = StudyRequest(
        email_subject="FDOT D1 SR 72",
        email_from="cseat@example.com",
        email_to="orders@qualitycounts.net",
        jurisdiction="FDOT D1",
        locations=tmcs + midblock + approaches,
    )

    # ---- Group + verify partitioning ----
    groups = _group_locations_for_qchub(req)
    total += 1
    if check(
        "Grouping produces 3 groups (7 TMCs / 28 approach tubes / 1 mid-block tube)",
        len(groups) == 3,
        f"got {len(groups)}",
    ):
        passed += 1

    # ---- Per-group KML: count placemarks matches the subset ----
    for i, g in enumerate(groups, 1):
        layer_name = _group_layer_name(g)
        kml = build_kml_for_locations(g["locations"], layer_name=layer_name)
        total += 1
        if check(
            f"Group {i} ({layer_name}): KML placemark_count matches subset size",
            kml.placemark_count == len(g["locations"]),
            f"placemarks={kml.placemark_count}, subset={len(g['locations'])}",
        ):
            passed += 1

        # The KML's <name> tag should be the layer_name
        text = kml.data.decode("utf-8")
        total += 1
        if check(
            f"Group {i}: KML <name> tag = layer_name",
            f"<name>{layer_name}</name>" in text or f"<name>{layer_name}" in text,
            f"layer_name={layer_name!r}",
        ):
            passed += 1

        # And the file should NOT contain locations from OTHER groups (sanity)
        other_locs = [loc for og in groups if og is not g for loc in og["locations"]]
        total += 1
        leaked = [loc.site_name for loc in other_locs if loc.site_name in text]
        if check(
            f"Group {i}: KML does NOT contain any other group's locations",
            not leaked,
            f"leaked: {leaked[:3]}" if leaked else "",
        ):
            passed += 1

    # ---- Total per-group placemarks = total request placemarks (no loss) ----
    total += 1
    sum_per_group = sum(len(g["locations"]) for g in groups)
    if check(
        "Sum of per-group placemarks = total locations (no loss in partition)",
        sum_per_group == 36,
        f"sum={sum_per_group}, request total=36",
    ):
        passed += 1

    # ---- Full-request build_kml still works (regression check) ----
    total += 1
    full = build_kml(req)
    if check(
        "build_kml(request) still produces all-placemarks KML (no regression)",
        full.placemark_count == 36,
        f"got {full.placemark_count}",
    ):
        passed += 1

    # ---- Layer-name format snapshot ----
    print()
    print("Layer names for FDOT scope:")
    for g in groups:
        print(f"  - {_group_layer_name(g)}")

    # ---- Survey path ----
    survey_loc = make_loc("Gap study spot", StudyKind.SURVEY, "vehicular_gap_study")
    survey_kml = build_kml_for_locations([survey_loc], layer_name="SURVEY gap test")
    total += 1
    if check(
        "Survey location KML builds cleanly (1 placemark)",
        survey_kml.placemark_count == 1,
        "",
    ):
        passed += 1

    # ---- Empty group (no geocoded locations) returns 0 placemarks, doesn't crash ----
    unplaced = StudyLocation(
        site_name="Unplaced",
        raw_text="x",
        address_or_intersection="x",
        study_kind=StudyKind.TUBE,
        tube_subtype=TubeSubtype.VOLUME,
        time_windows=[TimeWindow(label="x", start="00:00", end="23:59")],
        estimate=None,  # no coords
    )
    total += 1
    empty_kml = build_kml_for_locations([unplaced], layer_name="EMPTY GROUP")
    if check(
        "Locations without coords: KML placemark_count=0, skipped_unplaced=1",
        empty_kml.placemark_count == 0 and empty_kml.skipped_unplaced == 1,
        f"pm={empty_kml.placemark_count}, skipped={empty_kml.skipped_unplaced}",
    ):
        passed += 1

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
