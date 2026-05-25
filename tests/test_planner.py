"""Regression tests for `_group_locations_for_qchub`.

These pin down the grouping rules surfaced through 2026-05-17 testing:

  - TUBE keys by (study_kind, tube_subtype, time_windows). Each Tube
    subtype is its OWN qchub Study Group, even when time windows match
    — the user's correction after the FDOT D1 / SR 72 test (order 176459)
    where Volume,Class mid-block silently collapsed into a Volume group.

  - TMC keys by (study_kind, time_windows) — TMC has no group-stage
    subtype in qchub; outliers (Standard vs Large vs Complex) fix on
    the estimate modal post-submit.

  - SURVEY keys by (study_kind, time_windows, subtype, custom_name) —
    survey rows can't be re-categorized on the estimate modal, so
    subtype variance MUST be expressed as separate groups.

If these tests fail, the next live order will silently mis-bin locations
into wrong qchub groups — exactly the regression the user flagged.
"""
from __future__ import annotations

from collections import Counter

from traffic_intake.qchub import _group_locations_for_qchub


def _kinds_and_subtypes(groups) -> Counter:
    """Compact assertion helper: how many groups per (kind, subtype_label)."""
    return Counter((g["study_kind"], g["subtype_label"]) for g in groups)


def _location_counts_by_group(groups) -> dict[tuple[str, str], int]:
    return {
        (g["study_kind"], g["subtype_label"]): len(g["locations"])
        for g in groups
    }


# ----- TUBE: by-subtype splitting -----

def test_fdot_d1_splits_volume_from_volume_class(request_fdot_d1):
    """FDOT D1 has 7 TMCs + 28 Volume tubes + 1 Volume,Class midblock
    tube — all tubes share the 72-hour window. After the 2026-05-17
    fix, Volume and Volume,Class are SEPARATE groups (was 1 merged
    group of 29 with the midblock flagged as outlier)."""
    groups = _group_locations_for_qchub(request_fdot_d1)
    by_kind_sub = _location_counts_by_group(groups)

    assert by_kind_sub.get(("turning_movement", "Turn Count -- Standard")) == 7
    assert by_kind_sub.get(("tube", "Volume")) == 28
    assert by_kind_sub.get(("tube", "Volume, Class")) == 1
    assert len(groups) == 3  # No accidental extra groups.


def test_tube_groups_have_no_outliers(request_fdot_d1):
    """Tube groups are now homogeneous by construction — outliers can't
    exist. Field is kept for downstream uniformity but should be empty."""
    groups = _group_locations_for_qchub(request_fdot_d1)
    for g in groups:
        if g["study_kind"] == "tube":
            assert g["subtype_outliers"] == [], \
                f"Tube group {g['subtype_label']!r} has unexpected outliers: {g['subtype_outliers']}"


# ----- TMC: stays bundled, time windows preserved -----

def test_tmc_with_nonstandard_peaks_stays_one_group(request_nonstd_tmc_peaks):
    """4 TMCs with two non-canonical peak windows (06-09 + 16-19) →
    ONE group of 4 with both windows preserved. (Sub-strategy is
    'multi-period Specific Time' but the GROUPING is still one group.)"""
    groups = _group_locations_for_qchub(request_nonstd_tmc_peaks)
    assert len(groups) == 1
    g = groups[0]
    assert g["study_kind"] == "turning_movement"
    assert len(g["locations"]) == 4
    # Both time windows from the input must round-trip into the group.
    windows = g["time_windows"]
    assert len(windows) == 2
    assert (windows[0].start, windows[0].end) == ("06:00", "09:00")
    assert (windows[1].start, windows[1].end) == ("16:00", "19:00")


# ----- SURVEY: subtype + custom_name in the key -----

def test_survey_mixed_subtypes_split_to_separate_groups(request_survey_mixed_subtypes):
    """2 Queue + 2 Delay surveys sharing the AM peak → 2 groups, not 1.
    Subtype is part of the survey key because survey rows can't be
    re-categorized on the estimate modal."""
    groups = _group_locations_for_qchub(request_survey_mixed_subtypes)
    by_kind_sub = _location_counts_by_group(groups)

    assert by_kind_sub.get(("survey", "Queue Study")) == 2
    assert by_kind_sub.get(("survey", "Delay Study")) == 2
    assert len(groups) == 2


def test_survey_custom_video_groups_carry_custom_name(request_survey_custom_video):
    """3 Custom Video Survey locations with the same custom_name → 1 group,
    and the group dict's `survey_custom_name` is populated for downstream
    consumption (qchub's Custom Survey name input)."""
    groups = _group_locations_for_qchub(request_survey_custom_video)
    assert len(groups) == 1
    g = groups[0]
    assert g["study_kind"] == "survey"
    assert g["subtype_label"] == "Custom Video Survey..."  # ellipsis matches qchub option text
    assert g["survey_custom_name"] == "Pedestrian crossing observation"
    assert len(g["locations"]) == 3


# ----- Survey full-day still respects subtype grouping -----

def test_survey_full_day_video_surveillance(request_survey_full_day_video):
    """4 Video Surveillance survey locations sharing a 24h window → 1
    group. Subtype label matches the new VIDEO_SURVEILLANCE entry."""
    groups = _group_locations_for_qchub(request_survey_full_day_video)
    assert len(groups) == 1
    g = groups[0]
    assert g["study_kind"] == "survey"
    assert g["subtype_label"] == "Video Surveillance"
    assert len(g["locations"]) == 4


# ----- Deterministic ordering (regression test) -----

def test_planner_is_deterministic(request_fdot_d1):
    """Calling the planner twice on the same request must produce the
    same group ordering. Counter.most_common() is documented to break
    ties by insertion order — this test pins that contract so a future
    Python or library change doesn't silently reorder groups (which
    would break diff-based regression detection)."""
    g1 = _group_locations_for_qchub(request_fdot_d1)
    g2 = _group_locations_for_qchub(request_fdot_d1)
    assert [g["subtype_label"] for g in g1] == [g["subtype_label"] for g in g2]
    assert [len(g["locations"]) for g in g1] == [len(g["locations"]) for g in g2]
