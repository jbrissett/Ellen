"""Regression tests for StudyLocation's bidirectional `survey_custom_name`
validator.

Rule (per `feedback_qchub_subtype_layers.md` / model docstring):
  - `survey_custom_name` is REQUIRED iff `survey_subtype` is one of the
    Custom variants (CUSTOM_VIDEO_SURVEY or CUSTOM_NON_VIDEO_SURVEY).
  - For every other survey_subtype (or no subtype at all),
    `survey_custom_name` MUST be None.
  - Whitespace-only names normalize to None first so the rule has a
    single canonical "no name" state.

If these fail, the validator silently lets orphan / missing names land
on StudyLocations, which then propagates into qchub as wrong line
descriptions on the Custom Survey path.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from traffic_intake.models import (
    StudyKind,
    StudyLocation,
    SurveySubtype,
)


def _base_kwargs(**overrides) -> dict:
    kw = dict(
        site_name="x",
        raw_text="x",
        address_or_intersection="x, y, z",
        study_kind=StudyKind.SURVEY,
    )
    kw.update(overrides)
    return kw


# ----- happy paths (no error) -----

@pytest.mark.parametrize("custom_subtype", [
    SurveySubtype.CUSTOM_VIDEO_SURVEY,
    SurveySubtype.CUSTOM_NON_VIDEO_SURVEY,
])
def test_custom_subtype_with_name_is_valid(custom_subtype):
    loc = StudyLocation(**_base_kwargs(
        survey_subtype=custom_subtype,
        survey_custom_name="Pedestrian crossing count",
    ))
    assert loc.survey_custom_name == "Pedestrian crossing count"


@pytest.mark.parametrize("non_custom_subtype", [
    SurveySubtype.QUEUE_STUDY,
    SurveySubtype.DELAY_STUDY,
    SurveySubtype.VIDEO_SURVEILLANCE,
    SurveySubtype.VEHICULAR_GAP_STUDY,
    None,  # no subtype set yet
])
def test_non_custom_subtype_with_no_name_is_valid(non_custom_subtype):
    loc = StudyLocation(**_base_kwargs(
        survey_subtype=non_custom_subtype,
        survey_custom_name=None,
    ))
    assert loc.survey_custom_name is None


def test_whitespace_only_name_normalizes_to_none_on_non_custom():
    """Whitespace-only name is treated as 'no name' so it doesn't trigger
    the orphan-name failure on a non-custom subtype."""
    loc = StudyLocation(**_base_kwargs(
        survey_subtype=SurveySubtype.QUEUE_STUDY,
        survey_custom_name="   ",
    ))
    assert loc.survey_custom_name is None


# ----- failure paths -----

@pytest.mark.parametrize("custom_subtype", [
    SurveySubtype.CUSTOM_VIDEO_SURVEY,
    SurveySubtype.CUSTOM_NON_VIDEO_SURVEY,
])
def test_custom_subtype_without_name_raises(custom_subtype):
    with pytest.raises(ValidationError) as exc:
        StudyLocation(**_base_kwargs(survey_subtype=custom_subtype, survey_custom_name=None))
    assert "survey_custom_name is required" in str(exc.value)


@pytest.mark.parametrize("custom_subtype", [
    SurveySubtype.CUSTOM_VIDEO_SURVEY,
    SurveySubtype.CUSTOM_NON_VIDEO_SURVEY,
])
def test_custom_subtype_with_whitespace_only_name_raises(custom_subtype):
    """Whitespace-only normalizes to None first, then fails the required
    check — same outcome as passing None, different path."""
    with pytest.raises(ValidationError) as exc:
        StudyLocation(**_base_kwargs(survey_subtype=custom_subtype, survey_custom_name="   "))
    assert "survey_custom_name is required" in str(exc.value)


@pytest.mark.parametrize("non_custom_subtype", [
    SurveySubtype.QUEUE_STUDY,
    SurveySubtype.DELAY_STUDY,
    SurveySubtype.VIDEO_SURVEILLANCE,
    None,
])
def test_orphan_name_on_non_custom_raises(non_custom_subtype):
    """The orphan-name case — a custom_name set when the subtype isn't
    a Custom variant. This is the bug class the validator exists to
    catch: a leftover name from a subtype the LLM later changed away
    from (or set wrongly to begin with)."""
    with pytest.raises(ValidationError) as exc:
        StudyLocation(**_base_kwargs(
            survey_subtype=non_custom_subtype,
            survey_custom_name="leftover from a different subtype",
        ))
    assert "survey_custom_name must be None" in str(exc.value)
