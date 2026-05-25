"""Regression tests for `_duration_h_m` and `_group_layer_name`.

These cover the 1-day duration bug surfaced in the user's 2026-05-17 survey
test: extractor expressed "all day" as `00:00-23:59` (since `24:00` isn't a
legal time string), and the old `_hours_between` integer-divided 1439 min
→ 23 hours, which qchub silently entered as a 23-hour study. The fix in
`_duration_h_m` rounds anything within 1 minute of 24h up to (24, 0) AND
fills the previously-dropped Minutes field on the qchub form.

If any of these tests fail, the next live run will silently underbill a
1-day study or drop fractional-hour windows — both observed bugs.
"""
from __future__ import annotations

import pytest

from traffic_intake.qchub import _duration_h_m, _group_layer_name
from traffic_intake.models import TimeWindow


# ----- _duration_h_m -----

@pytest.mark.parametrize("start, end, total_hours, expected", [
    # The exact bug the user hit: LLM emits 00:00-23:59 for "all day"
    # because 24:00 isn't a valid HH:MM string. Must round up to 24h0m.
    ("00:00", "23:59", None, (24, 0)),
    # Overnight / same-time edge case → treat as full day.
    ("00:00", "00:00", None, (24, 0)),
    # Canonical AM peak (2-hr window) — most common TMC case.
    ("07:00", "09:00", None, (2, 0)),
    # 12-hr TMC window.
    ("07:00", "19:00", None, (12, 0)),
    # Fractional-hour window: 2.5h must yield 2h30m, NOT 2h0m. Prior
    # `_hours_between` dropped the 30 min via integer division.
    ("07:00", "09:30", None, (2, 30)),
    # 3-hour non-standard peak from PWC (Gorove/Slade).
    ("06:00", "09:00", None, (3, 0)),
    # When extractor sets `total_hours`, it wins over start/end.
    ("00:00", "23:59", 72, (72, 0)),
    ("00:00", "23:59", 168, (168, 0)),  # 1-week tube
    # Bad input → safe default (1h0m). Doesn't crash.
    ("garbage", "X", None, (1, 0)),
    ("", "", None, (1, 0)),
])
def test_duration_h_m(start, end, total_hours, expected):
    assert _duration_h_m(start, end, total_hours) == expected


# ----- _group_layer_name -----
# Format is what shows up as the MyMaps layer name AND in qchub diagnostic
# logs. Bug case: full-day windows used to render as "(00:00-23:59)" which
# leaked the 23:59 quirk into client-facing artifacts. Now: "(1 day count)".

def _make_group(study_kind: str, subtype_label: str, windows: list[TimeWindow]):
    """Minimal planner-group dict for layer-name tests (mirrors the shape
    `_group_locations_for_qchub` produces — only the fields _group_layer_name
    reads)."""
    return {
        "study_kind": study_kind,
        "subtype_label": subtype_label,
        "time_windows": windows,
    }


def test_layer_name_full_day_renders_as_one_day_count():
    g = _make_group("tube", "Volume", [TimeWindow(label="24h", start="00:00", end="23:59")])
    assert _group_layer_name(g) == "TUBE Volume (1 day count)"


def test_layer_name_72hour_count_renders_as_three_day_count():
    g = _make_group("tube", "Volume", [
        TimeWindow(label="72-hr", start="00:00", end="23:59", total_hours=72),
    ])
    assert _group_layer_name(g) == "TUBE Volume (3 day count)"


def test_layer_name_one_week_tube():
    g = _make_group("tube", "Volume, Class", [
        TimeWindow(label="1-week", start="00:00", end="23:59", total_hours=168),
    ])
    assert _group_layer_name(g) == "TUBE Volume, Class (7 day count)"


def test_layer_name_am_peak_unchanged():
    """Non-full-day windows still use the start-end format."""
    g = _make_group("turning_movement", "Turn Count -- Standard", [
        TimeWindow(label="AM Peak", start="07:00", end="09:00"),
    ])
    assert _group_layer_name(g) == "TURNING_MOVEMENT Turn Count -- Standard (07:00-09:00)"


def test_layer_name_multi_window_indicator():
    """When there are multiple windows, label appends a '+N more' hint."""
    g = _make_group("turning_movement", "Turn Count -- Standard", [
        TimeWindow(label="AM Peak", start="06:00", end="09:00"),
        TimeWindow(label="PM Peak", start="16:00", end="19:00"),
    ])
    assert "(06:00-09:00 (+1 more))" in _group_layer_name(g)


def test_layer_name_no_windows():
    g = _make_group("survey", "Queue Study", [])
    assert _group_layer_name(g) == "SURVEY Queue Study (no time window)"
