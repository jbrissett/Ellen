"""Regression tests for the qchub subtype label maps.

These guard against two real bug shapes:

  1. **Missing enum → label mapping**: when SurveySubtype gained 19 new
     members 2026-05-17 PM, the label map had to expand to match.
     Without these checks, a future enum addition silently produces
     KeyError at runtime when the planner tries to label that subtype.

  2. **Subtle label-text drift**: the Custom subtypes' option text in
     qchub's dropdown ends with `...` (verified via DevTools 2026-05-17).
     Without the ellipsis in our label, `select_option(label=...)`
     silently fails to match and the group falls through to qchub's
     placeholder default (the "Service Subtype" entry). This test pins
     the exact text the qchub UI uses.
"""
from __future__ import annotations

import pytest

from traffic_intake.models import (
    StudyKind,
    StudyLocation,
    SurveySubtype,
    TMCSubtype,
    TubeSubtype,
)
from traffic_intake.qchub import (
    _qchub_subtype_label,
    _SURVEY_DEFAULT_LABEL,
    _TMC_DEFAULT_LABEL,
    _TUBE_DEFAULT_LABEL,
)


# ----- enum / label map completeness -----

def test_every_survey_subtype_has_a_label():
    """If you add a SurveySubtype member without updating _SURVEY_DEFAULT_LABEL,
    this fails — preventing a runtime KeyError when the planner tries to
    label that subtype's group."""
    missing = [s for s in SurveySubtype if s not in _SURVEY_DEFAULT_LABEL]
    assert not missing, f"Missing label entries for survey subtypes: {missing}"


def test_every_tube_subtype_has_a_label():
    missing = [s for s in TubeSubtype if s not in _TUBE_DEFAULT_LABEL]
    assert not missing, f"Missing label entries for tube subtypes: {missing}"


def test_every_tmc_subtype_has_a_label():
    missing = [s for s in TMCSubtype if s not in _TMC_DEFAULT_LABEL]
    assert not missing, f"Missing label entries for TMC subtypes: {missing}"


# ----- Custom Survey label has the qchub-required ellipsis -----

def test_custom_video_survey_label_has_ellipsis():
    """qchub's actual dropdown option is 'Custom Video Survey...' — without
    the ellipsis, select_option(label=...) misses and the group lands on
    the placeholder default. Verified via DevTools 2026-05-17 PM."""
    assert _SURVEY_DEFAULT_LABEL[SurveySubtype.CUSTOM_VIDEO_SURVEY] == "Custom Video Survey..."


def test_custom_non_video_survey_label_has_ellipsis():
    assert _SURVEY_DEFAULT_LABEL[SurveySubtype.CUSTOM_NON_VIDEO_SURVEY] == "Custom Non-Video Survey..."


def test_video_surveillance_label_exact():
    """User-direction subtype 2026-05-17 PM: 'Video Surveillance' is the
    second-most-common video survey type (after Custom Video). Without
    this exact label, Ellen can't auto-pick it."""
    assert _SURVEY_DEFAULT_LABEL[SurveySubtype.VIDEO_SURVEILLANCE] == "Video Surveillance"


# ----- _qchub_subtype_label dispatch -----

def test_subtype_label_dispatches_by_kind():
    """The dispatcher returns the right map's label for each kind."""
    tmc_loc = StudyLocation(
        site_name="x", raw_text="x", address_or_intersection="x, y, z",
        study_kind=StudyKind.TURNING_MOVEMENT,
        tmc_subtype=TMCSubtype.COMPLEX,
    )
    assert _qchub_subtype_label(tmc_loc) == _TMC_DEFAULT_LABEL[TMCSubtype.COMPLEX]

    tube_loc = StudyLocation(
        site_name="x", raw_text="x", address_or_intersection="x, y, z",
        study_kind=StudyKind.TUBE,
        tube_subtype=TubeSubtype.VOLUME_SPEED_CLASS,
    )
    assert _qchub_subtype_label(tube_loc) == _TUBE_DEFAULT_LABEL[TubeSubtype.VOLUME_SPEED_CLASS]

    survey_loc = StudyLocation(
        site_name="x", raw_text="x", address_or_intersection="x, y, z",
        study_kind=StudyKind.SURVEY,
        survey_subtype=SurveySubtype.QUEUE_STUDY,
    )
    assert _qchub_subtype_label(survey_loc) == "Queue Study"


def test_subtype_label_defaults_when_subtype_missing():
    """When a location has no explicit subtype, the dispatcher returns the
    kind's documented default (TMC=Standard, Tube=Volume, Survey=Vehicular
    Gap). Tested so a future change to the defaults is intentional."""
    for kind, default_label in [
        (StudyKind.TURNING_MOVEMENT, _TMC_DEFAULT_LABEL[TMCSubtype.STANDARD]),
        (StudyKind.TUBE, _TUBE_DEFAULT_LABEL[TubeSubtype.VOLUME]),
        (StudyKind.SURVEY, _SURVEY_DEFAULT_LABEL[SurveySubtype.VEHICULAR_GAP_STUDY]),
    ]:
        loc = StudyLocation(
            site_name="x", raw_text="x", address_or_intersection="x, y, z",
            study_kind=kind,
        )
        assert _qchub_subtype_label(loc) == default_label, (
            f"Default label for {kind.value!r} should be {default_label!r}"
        )


@pytest.mark.parametrize("subtype, expected_label", [
    (SurveySubtype.BLUETOOTH_SURVEY, "Bluetooth Survey"),
    (SurveySubtype.DELAY_STUDY, "Delay Study"),
    (SurveySubtype.VEHICULAR_GAP_STUDY, "Vehicular Gap Study (Video)"),
    (SurveySubtype.PEDESTRIAN_VOLUME, "Pedestrian Volume Counts"),
    (SurveySubtype.LICENSE_PLATE_OD, "License Plate O-D Study"),
    (SurveySubtype.HORIZONTAL_CURVE_ADVISORY_SPEED, "Horizontal Curve Advisory Speed Survey"),
])
def test_survey_subtype_labels_match_qchub_dropdown(subtype, expected_label):
    """Spot-check key survey labels against the qchub dropdown text the
    user verified via DevTools. These are the ones most likely to drift
    if someone renames the enum and forgets the label."""
    assert _SURVEY_DEFAULT_LABEL[subtype] == expected_label
