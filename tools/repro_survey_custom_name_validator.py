"""Exercise StudyLocation's bidirectional survey_custom_name validator.

Covers:
  1. CUSTOM_VIDEO_SURVEY + name           -> OK
  2. CUSTOM_VIDEO_SURVEY + None           -> ValueError
  3. CUSTOM_VIDEO_SURVEY + whitespace     -> ValueError (normalizes to None)
  4. QUEUE_STUDY + name                   -> ValueError (orphan name)
  5. None subtype + name                  -> ValueError
  6. QUEUE_STUDY + None                   -> OK
  7. None subtype + None                  -> OK
  8. QUEUE_STUDY + whitespace             -> OK (normalizes to None)
  9. TUBE + tube_subtype + None           -> OK (non-survey path unaffected)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Repo-relative import.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pydantic import ValidationError  # noqa: E402

from traffic_intake.models import (  # noqa: E402
    StudyKind,
    StudyLocation,
    SurveySubtype,
    TubeSubtype,
)


def _base(**overrides):
    kw = dict(
        site_name="Test site",
        raw_text="test",
        address_or_intersection="1st & Main, Salt Lake City, UT",
        study_kind=StudyKind.SURVEY,
    )
    kw.update(overrides)
    return kw


def expect_ok(label: str, **kw):
    try:
        loc = StudyLocation(**_base(**kw))
        print(f"PASS  {label}  ->  survey_custom_name={loc.survey_custom_name!r}")
        return loc
    except ValidationError as exc:
        print(f"FAIL  {label}  unexpected ValidationError: {exc.errors()[0]['msg']}")
        return None


def expect_fail(label: str, **kw):
    try:
        StudyLocation(**_base(**kw))
        print(f"FAIL  {label}  expected ValidationError, got success")
    except ValidationError as exc:
        print(f"PASS  {label}  -> {exc.errors()[0]['msg']}")


print("=== StudyLocation survey_custom_name validator ===\n")

# 1. happy path
expect_ok(
    "1. CUSTOM_VIDEO_SURVEY + name",
    survey_subtype=SurveySubtype.CUSTOM_VIDEO_SURVEY,
    survey_custom_name="Pedestrian crossing count",
)

# 2. custom-video missing required name
expect_fail(
    "2. CUSTOM_VIDEO_SURVEY + None",
    survey_subtype=SurveySubtype.CUSTOM_VIDEO_SURVEY,
    survey_custom_name=None,
)

# 3. custom-video with whitespace-only name (normalizes -> None -> fails required)
expect_fail(
    "3. CUSTOM_VIDEO_SURVEY + '   '",
    survey_subtype=SurveySubtype.CUSTOM_VIDEO_SURVEY,
    survey_custom_name="   ",
)

# 4. orphan name on non-custom subtype (the bug class we're guarding)
expect_fail(
    "4. QUEUE_STUDY + orphan name",
    survey_subtype=SurveySubtype.QUEUE_STUDY,
    survey_custom_name="leftover from custom video",
)

# 5. orphan name with no subtype at all
expect_fail(
    "5. None subtype + name",
    survey_subtype=None,
    survey_custom_name="should not be here",
)

# 6. normal queue study, no name
expect_ok(
    "6. QUEUE_STUDY + None",
    survey_subtype=SurveySubtype.QUEUE_STUDY,
    survey_custom_name=None,
)

# 7. survey with no subtype yet, no name
expect_ok(
    "7. None subtype + None",
    survey_subtype=None,
    survey_custom_name=None,
)

# 8. whitespace name on non-custom subtype normalizes to None (no error)
expect_ok(
    "8. QUEUE_STUDY + '   ' (normalizes to None)",
    survey_subtype=SurveySubtype.QUEUE_STUDY,
    survey_custom_name="   ",
)

# 9. unrelated tube path unaffected
expect_ok(
    "9. TUBE + tube_subtype + None",
    study_kind=StudyKind.TUBE,
    tube_subtype=TubeSubtype.VIDEO_ATR_VOLUME,
    survey_subtype=None,
    survey_custom_name=None,
)
