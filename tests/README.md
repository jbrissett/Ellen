# Traffic Intake — Regression Tests

A small, fast pytest suite that pins down behaviour the user has explicitly
hit during testing. Each test corresponds to a bug shape we shipped a fix
for. The suite runs in well under a second; no LLM, no network, no qchub
browser.

## Running

Easiest: double-click `tools\Run Regression Tests.bat` from the repo root.
Auto-installs pytest into the venv on first run.

From the command line:

```
.venv\Scripts\python.exe -m pytest tests\ -v
```

## What's covered

| File | Bug shape it pins |
|---|---|
| `test_duration.py` | The 24-hour translation bug (LLM emits `00:00-23:59` for "all day"; qchub Hours field must get `24`, not `23`). Also covers full-day map-layer labels (`1 day count` not `00:00-23:59`). |
| `test_planner.py` | Tube + Survey now split by subtype at planning time; TMC stays bundled (no group-stage subtype dropdown). FDOT D1 / SR 72 case (Volume vs Volume,Class) lives here. |
| `test_subtype_labels.py` | The 22-entry `_SURVEY_DEFAULT_LABEL` map is complete; Custom Video Survey / Custom Non-Video Survey labels have the trailing `...` to match qchub's actual dropdown text. |
| `test_validators.py` | `StudyLocation.survey_custom_name` bidirectional rule — required ↔ custom subtype, must be None otherwise, for BOTH `CUSTOM_VIDEO_SURVEY` and `CUSTOM_NON_VIDEO_SURVEY`. |

## When to run

Before shipping any change that touched `qchub.py`, `models.py`, or
`chat.py`. The recent ship cadence has been "fix one thing, silently
break an older thing." This suite is the cheap hedge against that.

## When to add a test

Add one as soon as the user reports a behavior bug AND we know how to
pin the rule in pure-Python code (no qchub browser required). The
pattern is:

1. Add a fixture to `conftest.py` if the test needs a new StudyRequest shape.
2. Add a test_*.py that exercises the rule with parametrized cases.
3. Run the suite; confirm the new test goes red against the broken
   code BEFORE the fix, and green after.

What's NOT covered here (deliberate):

- The qchub browser pipeline (requires live network + auth).
- MyMaps automation (same).
- The extractor LLM call (non-deterministic; would need recorded
  fixtures or VCR-style cassettes).
- Ellen's chat tool dispatch (covered indirectly via the underlying
  pure functions these tests exercise).

When the user reports a bug in one of those areas, the diagnosis path
is still: read the run.log, write a one-off `tools/repro_*.py`, fix,
optionally promote the repro into this suite if the rule is testable
deterministically.
