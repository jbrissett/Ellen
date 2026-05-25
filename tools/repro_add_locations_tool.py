"""Unit test for Ellen's new add_locations tool.

Mocks the geocoder so the test is offline and deterministic. Verifies:

- Multiple locations geocoded in one call all get appended
- Per-item failures (geocoder None / hard error) don't drop the whole batch
- Returned JSON summary has accurate counts + per-item detail
- Subtype validation works (invalid subtype reported, valid items still land)
- The FDOT D1 SR 72 scenario (7 TMCs + 28 approach tubes + 1 mid-block) builds cleanly

Run: python tools/repro_add_locations_tool.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from traffic_intake.chat import execute_tool  # type: ignore
from traffic_intake.geocoder import GeocodeResult  # type: ignore
from traffic_intake.models import StudyKind, StudyRequest  # type: ignore


def make_empty_request() -> StudyRequest:
    return StudyRequest(
        email_subject="SR 72 FDOT D1",
        email_from="cseat@example.com",
        email_to="orders@qualitycounts.net",
        jurisdiction="FDOT D1",
        locations=[],
    )


def fake_geocoder(address: str, *, api_key=None) -> GeocodeResult | None:
    """Mock geocoder: returns a deterministic result for any address mentioning
    a recognizable street name; None for unmatchable ones.
    """
    if "definitely-not-a-real-place" in address.lower():
        return None
    # Hash-ish deterministic lat/lon so each address gets a stable result.
    lat = 27.3 + (hash(address) % 1000) / 100000.0
    lon = -82.4 + (hash(address) % 500) / 100000.0
    return GeocodeResult(
        latitude=round(lat, 6),
        longitude=round(lon, 6),
        formatted_address=address.split(",")[0].strip(),
        location_type="GEOMETRIC_CENTER",  # → confidence "medium"
        place_id=f"place_{abs(hash(address)) % 999999}",
    )


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"  {status}  {label}  {detail}")
    return condition


def main() -> int:
    passed = 0
    total = 0

    # Patch the geocoder.geocode that chat.py imports as `geocoder.geocode`.
    with patch("traffic_intake.chat.geocoder.geocode", side_effect=fake_geocoder):
        # ---- Case 1: add 3 TMCs in one call ----
        req = make_empty_request()
        total += 1
        result = execute_tool("add_locations", {"locations": [
            {
                "site_name": "SR 72 & Proctor Rd",
                "address_or_intersection": "SR 72 and Proctor Rd, Sarasota, FL",
                "study_kind": "turning_movement",
                "subtype": "standard",
                "time_windows": [{"label": "7am-7pm", "start": "07:00", "end": "19:00"}],
            },
            {
                "site_name": "SR 72 & Preservation Dr",
                "address_or_intersection": "SR 72 and Preservation Dr, Sarasota, FL",
                "study_kind": "turning_movement",
                "subtype": "standard",
                "time_windows": [{"label": "7am-7pm", "start": "07:00", "end": "19:00"}],
            },
            {
                "site_name": "SR 72 & Lorraine Rd",
                "address_or_intersection": "SR 72 and Lorraine Rd, Sarasota, FL",
                "study_kind": "turning_movement",
                "subtype": "standard",
                "time_windows": [{"label": "7am-7pm", "start": "07:00", "end": "19:00"}],
            },
        ]}, req)
        summary = json.loads(result)
        if check(
            "Added 3 TMCs in one call",
            summary["added"] == 3 and len(req.locations) == 3,
            f"added={summary['added']}, locations={len(req.locations)}",
        ):
            passed += 1

        # ---- Case 2: one failing geocode in a batch doesn't kill the others ----
        req = make_empty_request()
        total += 1
        result = execute_tool("add_locations", {"locations": [
            {
                "site_name": "Good site",
                "address_or_intersection": "SR 72 and Proctor Rd, Sarasota, FL",
                "study_kind": "tube",
                "subtype": "volume",
            },
            {
                "site_name": "Bad site",
                "address_or_intersection": "definitely-not-a-real-place",
                "study_kind": "tube",
                "subtype": "volume",
            },
            {
                "site_name": "Good site 2",
                "address_or_intersection": "Lorraine Rd, Sarasota, FL",
                "study_kind": "tube",
                "subtype": "volume",
            },
        ]}, req)
        summary = json.loads(result)
        if check(
            "Partial failure: 2 added, 1 failed",
            summary["added"] == 2 and summary["failed"] == 1
            and len(req.locations) == 2
            and summary["failures"][0]["site_name"] == "Bad site",
            f"added={summary['added']}, failed={summary['failed']}",
        ):
            passed += 1

        # ---- Case 3: invalid subtype reported per-item ----
        req = make_empty_request()
        total += 1
        result = execute_tool("add_locations", {"locations": [
            {
                "site_name": "Has bad subtype",
                "address_or_intersection": "SR 72 and Proctor Rd, Sarasota, FL",
                "study_kind": "tube",
                "subtype": "standard",  # 'standard' is TMC, not tube → should fail
            },
        ]}, req)
        summary = json.loads(result)
        if check(
            "Bad subtype caught per-item",
            summary["added"] == 0 and summary["failed"] == 1
            and "not a valid tube subtype" in summary["failures"][0]["reason"],
            f"failure: {summary.get('failures')}",
        ):
            passed += 1

        # ---- Case 4: Survey kind with vehicular_gap_study subtype ----
        req = make_empty_request()
        total += 1
        result = execute_tool("add_locations", {"locations": [
            {
                "site_name": "Gap study point",
                "address_or_intersection": "SR 72 east of Lorraine, Sarasota, FL",
                "study_kind": "survey",
                "subtype": "vehicular_gap_study",
                "time_windows": [{"label": "7am-7pm", "start": "07:00", "end": "19:00"}],
            },
        ]}, req)
        summary = json.loads(result)
        if check(
            "Survey/Vehicular Gap Study location added with coords",
            summary["added"] == 1
            and req.locations[0].study_kind == StudyKind.SURVEY
            and req.locations[0].survey_subtype is not None
            and req.locations[0].estimate is not None
            and req.locations[0].estimate.source == "geocoded",
            "",
        ):
            passed += 1

        # ---- Case 5: empty list is a no-op ----
        req = make_empty_request()
        total += 1
        result = execute_tool("add_locations", {"locations": []}, req)
        if check(
            "Empty locations list is a no-op",
            "No-op" in result and len(req.locations) == 0,
            f"result={result[:60]!r}",
        ):
            passed += 1

        # ---- Case 6: the FDOT D1 SR 72 scenario ----
        # 7 TMCs + 28 approach tubes (4 per intersection) + 1 mid-block tube = 36 locations
        intersections = [
            ("Proctor Rd", "Proctor"),
            ("Preservation Dr", "Preservation"),
            ("Aventura Dr", "Aventura"),
            ("Churchill Downs Rd", "Churchill Downs"),
            ("Coash Rd", "Coash"),
            ("Timberland Ln", "Timberland"),
            ("Lorraine Rd", "Lorraine"),
        ]
        req = make_empty_request()
        total += 1

        # Batch 1: 7 TMCs
        tmc_batch = [
            {
                "site_name": f"SR 72 & {full}",
                "address_or_intersection": f"SR 72 and {full}, Sarasota, FL",
                "study_kind": "turning_movement",
                "subtype": "standard",
                "time_windows": [{"label": "7am-7pm", "start": "07:00", "end": "19:00"}],
            }
            for full, _short in intersections
        ]
        execute_tool("add_locations", {"locations": tmc_batch}, req)

        # Batch 2: 28 approach tubes (4 per intersection)
        tube_batch = []
        for full, _short in intersections:
            for leg in ("N approach", "S approach", "E approach", "W approach"):
                tube_batch.append({
                    "site_name": f"SR 72 & {full} — {leg}",
                    "address_or_intersection": f"SR 72 and {full}, Sarasota, FL",
                    "study_kind": "tube",
                    "subtype": "volume",
                    "time_windows": [{"label": "72h", "start": "00:00", "end": "23:59"}],
                })
        execute_tool("add_locations", {"locations": tube_batch}, req)

        # Batch 3: 1 mid-block tube
        execute_tool("add_locations", {"locations": [
            {
                "site_name": "SR 72 mid-block (Proctor to Lorraine)",
                "address_or_intersection": "SR 72 between Proctor Rd and Lorraine Rd, Sarasota, FL",
                "study_kind": "tube",
                "subtype": "volume_class",
                "time_windows": [{"label": "72h", "start": "00:00", "end": "23:59"}],
            },
        ]}, req)

        n_tmc = sum(1 for loc in req.locations if loc.study_kind == StudyKind.TURNING_MOVEMENT)
        n_tube = sum(1 for loc in req.locations if loc.study_kind == StudyKind.TUBE)
        if check(
            "FDOT D1 SR 72 scope built: 7 TMC + 29 Tube = 36 locations",
            len(req.locations) == 36 and n_tmc == 7 and n_tube == 29,
            f"total={len(req.locations)}, TMC={n_tmc}, Tube={n_tube}",
        ):
            passed += 1

    print(f"\nResult: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
