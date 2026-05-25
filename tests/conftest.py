"""Shared pytest fixtures for the regression suite.

The fixtures here build SYNTHETIC StudyRequest objects whose shape mirrors
real orders the user has run (FDOT D1 / SR 72, Prince William Survey,
Gorove Slade TMC, Dewberry-style multi-link). Tests use them to exercise
the planner, validator, and label-map code paths deterministically — no
LLM calls, no network, no qchub browser.

Add a fixture here when a new real-order shape surfaces in a user test.
That's the regression hedge: once a shape is in here, the next change
that would break it lights up red.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running pytest directly from the repo root without setting PYTHONPATH.
_REPO_SRC = Path(__file__).parent.parent / "src"
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

import pytest  # noqa: E402

from traffic_intake.models import (  # noqa: E402
    LocationEstimate,
    StudyKind,
    StudyLocation,
    StudyRequest,
    SurveySubtype,
    TimeWindow,
    TMCSubtype,
    TubeSubtype,
)


def _loc(
    *,
    name: str,
    kind: StudyKind,
    tmc_sub: TMCSubtype | None = None,
    tube_sub: TubeSubtype | None = None,
    survey_sub: SurveySubtype | None = None,
    custom_name: str | None = None,
    tws: list[TimeWindow] | None = None,
    lat: float = 27.27,
    lon: float = -82.40,
) -> StudyLocation:
    """Build a StudyLocation with sensible defaults for tests."""
    return StudyLocation(
        site_name=name,
        raw_text=name,
        address_or_intersection=f"{name}, Sarasota, FL",
        study_kind=kind,
        tmc_subtype=tmc_sub,
        tube_subtype=tube_sub,
        survey_subtype=survey_sub,
        survey_custom_name=custom_name,
        time_windows=tws or [],
        estimate=LocationEstimate(
            latitude=lat, longitude=lon, confidence="high", source="kmz",
        ),
    )


# ----- shared TimeWindow templates -----

_PEAK_AM = TimeWindow(label="AM Peak", start="07:00", end="09:00")
_PEAK_PM = TimeWindow(label="PM Peak", start="16:00", end="18:00")
_NONSTD_AM = TimeWindow(label="AM Peak", start="06:00", end="09:00")   # 3-hour, non-canonical
_NONSTD_PM = TimeWindow(label="PM Peak", start="16:00", end="19:00")   # 3-hour, non-canonical
_TWELVE_HR = TimeWindow(label="12-hr", start="07:00", end="19:00")
_FULL_DAY = TimeWindow(label="24-hr", start="00:00", end="23:59")
_TUBE_72H = TimeWindow(label="72-hr", start="00:00", end="23:59", total_hours=72)


# ----- fixtures: real-order shapes -----

@pytest.fixture
def request_fdot_d1() -> StudyRequest:
    """7 TMCs (12-hr) + 28 Volume tube approaches (72-hr) + 1 Volume,Class
    midblock tube (72-hr) — the FDOT D1 / SR 72 scope from order 176459.
    Tests tube-subtype split (Volume vs Volume,Class) and TMC bundling.
    """
    locations: list[StudyLocation] = []
    tmc_names = ["Lorraine", "Proctor", "Preservation", "Aventura",
                 "Churchill Downs", "Coash", "Timberland"]
    locations += [
        _loc(name=f"SR 72 -- {n}", kind=StudyKind.TURNING_MOVEMENT,
             tmc_sub=TMCSubtype.STANDARD, tws=[_TWELVE_HR])
        for n in tmc_names
    ]
    locations += [
        _loc(name=f"SR 72 & {inter} -- {leg} approach", kind=StudyKind.TUBE,
             tube_sub=TubeSubtype.VOLUME, tws=[_TUBE_72H])
        for inter in tmc_names for leg in ["N", "S", "E", "W"]
    ]
    locations.append(
        _loc(name="SR 72 Midblock (Proctor to Lorraine)", kind=StudyKind.TUBE,
             tube_sub=TubeSubtype.VOLUME_CLASS, tws=[_TUBE_72H])
    )
    return StudyRequest(
        email_subject="FDOT D1 — SR 72",
        email_from="andrew@kimley-horn.com",
        email_to="data-entry@qualitycounts.net",
        locations=locations,
    )


@pytest.fixture
def request_nonstd_tmc_peaks() -> StudyRequest:
    """4 TMCs with non-standard peaks (06:00-09:00 AM + 16:00-19:00 PM).
    Tests the multi-period Specific Time path that replaced the
    "exactly one specific-time window or bail" path. From order 176459.
    """
    locations = [
        _loc(name=f"Site {i}", kind=StudyKind.TURNING_MOVEMENT,
             tmc_sub=TMCSubtype.STANDARD, tws=[_NONSTD_AM, _NONSTD_PM])
        for i in range(1, 5)
    ]
    return StudyRequest(
        email_subject="PWC AM/PM Peaks",
        email_from="mk@goroveslade.com",
        email_to="data-entry@qualitycounts.net",
        locations=locations,
    )


@pytest.fixture
def request_survey_full_day_video() -> StudyRequest:
    """4 Video Surveillance survey locations with a single full-day
    (00:00-23:59) window. Tests the 1-day duration fix (must render as
    24h, not 23h59m) and the simplified Survey-subtype handling.
    """
    locations = [
        _loc(name=f"Survey site {i}", kind=StudyKind.SURVEY,
             survey_sub=SurveySubtype.VIDEO_SURVEILLANCE, tws=[_FULL_DAY])
        for i in range(1, 5)
    ]
    return StudyRequest(
        email_subject="24-Hour Video Collection",
        email_from="justin@psi-engineering.com",
        email_to="data-entry@qualitycounts.net",
        locations=locations,
    )


@pytest.fixture
def request_survey_mixed_subtypes() -> StudyRequest:
    """Survey locations with two different subtypes (Queue + Delay) sharing
    the same time window. Tests that survey-by-subtype grouping splits them
    into separate groups (because survey rows can't be re-categorized on
    the estimate modal — see project_qchub_subtype_layers.md).
    """
    locations = [
        _loc(name=f"Queue site {i}", kind=StudyKind.SURVEY,
             survey_sub=SurveySubtype.QUEUE_STUDY, tws=[_PEAK_AM])
        for i in range(1, 3)
    ] + [
        _loc(name=f"Delay site {i}", kind=StudyKind.SURVEY,
             survey_sub=SurveySubtype.DELAY_STUDY, tws=[_PEAK_AM])
        for i in range(1, 3)
    ]
    return StudyRequest(
        email_subject="Mixed Survey",
        email_from="planner@example.com",
        email_to="data-entry@qualitycounts.net",
        locations=locations,
    )


@pytest.fixture
def request_survey_custom_video() -> StudyRequest:
    """Survey locations with CUSTOM_VIDEO_SURVEY subtype + a shared
    `survey_custom_name`. Tests that the validator accepts both Custom
    subtypes WITH name (and only with name), and that the planner uses
    the custom name as part of the group key.
    """
    locations = [
        _loc(name=f"Pedestrian site {i}", kind=StudyKind.SURVEY,
             survey_sub=SurveySubtype.CUSTOM_VIDEO_SURVEY,
             custom_name="Pedestrian crossing observation", tws=[_PEAK_AM])
        for i in range(1, 4)
    ]
    return StudyRequest(
        email_subject="Custom Video Survey",
        email_from="planner@example.com",
        email_to="data-entry@qualitycounts.net",
        locations=locations,
    )
