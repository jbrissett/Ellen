"""Unit test for Ellen's pre-flight validate_for_qchub tool.

Reconstructs the FDOT D1 SR 72 failure (7 TMCs with windows + 29 tubes WITHOUT
windows) and verifies validate_for_qchub flags every single tube as needing
time_windows. Also tests clean / partially-clean states.

Run: python tools/repro_validate_for_qchub.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from traffic_intake.chat import execute_tool  # type: ignore
from traffic_intake.models import (  # type: ignore
    LocationEstimate, StudyKind, StudyLocation, StudyRequest, TimeWindow, TMCSubtype, TubeSubtype,
)


def make_loc(name, kind, subtype, with_windows=True, with_estimate=True) -> StudyLocation:
    loc = StudyLocation(
        site_name=name,
        raw_text=name,
        address_or_intersection=name,
        study_kind=kind,
        time_windows=(
            [TimeWindow(label="7am-7pm", start="07:00", end="19:00")] if with_windows else []
        ),
        estimate=(
            LocationEstimate(latitude=27.3, longitude=-82.4, confidence="medium", source="geocoded")
            if with_estimate else None
        ),
    )
    if kind == StudyKind.TURNING_MOVEMENT:
        loc.tmc_subtype = TMCSubtype(subtype) if subtype else None
    elif kind == StudyKind.TUBE:
        loc.tube_subtype = TubeSubtype(subtype) if subtype else None
    return loc


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {label}  {detail}")
    return condition


def main() -> int:
    passed = 0
    total = 0

    # ---- Case 1: FDOT scenario reconstruction — 7 TMCs OK, 29 tubes missing windows ----
    locs = []
    for i in range(7):
        locs.append(make_loc(f"TMC {i}", StudyKind.TURNING_MOVEMENT, "standard"))
    for i in range(29):
        locs.append(make_loc(f"Tube {i}", StudyKind.TUBE, "volume", with_windows=False))
    req = StudyRequest(
        email_subject="x",
        email_from="x@x.com",
        email_to="y@y.com",
        client_company="Kimley-Horn",
        client_contact_email="a.stastny@kimley-horn.com",
        locations=locs,
    )
    total += 1
    result = execute_tool("validate_for_qchub", {}, req)
    report = json.loads(result)
    # All 29 tubes flagged for missing time_windows; 7 TMCs clean
    tube_errs = [
        i for i in report["issues"]
        if i.get("missing") and "time_windows" in " ".join(i["missing"])
    ]
    if check(
        "FDOT scenario — 29 tubes flagged for missing time_windows, ok=False",
        not report["ok"] and len(tube_errs) == 29,
        f"ok={report['ok']}, tube_errs={len(tube_errs)}, issue_count={report['issue_count']}",
    ):
        passed += 1

    # ---- Case 2: clean state — everything has windows + subtype + estimate ----
    locs_clean = [
        make_loc(f"TMC {i}", StudyKind.TURNING_MOVEMENT, "standard") for i in range(3)
    ] + [
        make_loc(f"Tube {i}", StudyKind.TUBE, "volume_speed") for i in range(3)
    ]
    req_clean = StudyRequest(
        email_subject="x",
        email_from="x@x.com",
        email_to="y@y.com",
        client_company="Kimley-Horn",
        client_contact_email="a@x.com",
        locations=locs_clean,
    )
    total += 1
    result = execute_tool("validate_for_qchub", {}, req_clean)
    report = json.loads(result)
    if check(
        "Clean state — ok=True, no errors",
        report["ok"] and report["issue_count"] == 0,
        f"ok={report['ok']}, issues={report['issue_count']}",
    ):
        passed += 1

    # ---- Case 3: empty request ----
    empty_req = StudyRequest(
        email_subject="x", email_from="x@x.com", email_to="y@y.com", locations=[],
    )
    total += 1
    result = execute_tool("validate_for_qchub", {}, empty_req)
    report = json.loads(result)
    if check(
        "Empty request — flagged with 'No locations' error",
        not report["ok"] and any("No locations" in i.get("message", "") for i in report["issues"]),
        f"ok={report['ok']}, issues={report['issues']}",
    ):
        passed += 1

    # ---- Case 4: missing top-level client fields surfaces as warning ----
    loc_only = StudyRequest(
        email_subject="x", email_from="x@x.com", email_to="y@y.com",
        locations=[make_loc("Site", StudyKind.TUBE, "volume")],
        # No client_company, no client_contact_email
    )
    total += 1
    result = execute_tool("validate_for_qchub", {}, loc_only)
    report = json.loads(result)
    has_warning = any(
        i.get("severity") == "warning" and "client_company" in i.get("message", "")
        for i in report["issues"]
    )
    # All location-level errors absent, but warning present, and ok=True (warnings don't block)
    if check(
        "Missing top-level client fields -> warning, not error",
        has_warning and report["ok"],
        f"has_warning={has_warning}, ok={report['ok']}",
    ):
        passed += 1

    # ---- Case 5: location missing estimate (coords) — flagged as error ----
    loc_no_coords = StudyRequest(
        email_subject="x", email_from="x@x.com", email_to="y@y.com",
        client_company="x", client_contact_email="a@x.com",
        locations=[make_loc("NoCoords", StudyKind.TUBE, "volume", with_estimate=False)],
    )
    total += 1
    result = execute_tool("validate_for_qchub", {}, loc_no_coords)
    report = json.loads(result)
    has_coord_err = any(
        i.get("severity") == "error" and "coordinates" in " ".join(i.get("missing", []))
        for i in report["issues"]
    )
    if check(
        "Location missing estimate/coords -> flagged as error",
        has_coord_err and not report["ok"],
        f"has_coord_err={has_coord_err}, ok={report['ok']}",
    ):
        passed += 1

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
