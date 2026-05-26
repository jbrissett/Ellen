"""Runtime settings centralized — read once, write from Settings dialog.

Lives outside the UI package so non-UI code (mymaps.py, qchub.py worker
threads) can read settings without importing PySide6 widgets. QSettings
itself is thread-safe across Qt6, so the Playwright worker thread can
call `is_headless_mode()` without coordination.

Why a dedicated module: settings keys are now read from four places
(Settings dialog, mymaps launch, qchub launch, future tray-icon
defaults). Centralizing prevents key-name typos (`headless` vs
`headless_browsers` vs `is_headless` would silently miss).
"""
from __future__ import annotations

from PySide6.QtCore import QSettings


# QSettings namespace — kept as "Traffic Intake" (not "Ellen") for
# back-compat with installs that already have keys saved under the
# original org/app pair.
_ORG = "Quality Counts"
_APP = "Traffic Intake"


# Default for the headless toggle. FALSE = browsers open visibly so the
# user can watch the MyMaps + qchub automation work. Flipped back from
# True → False 2026-05-26 per user direction: "default to off and I'll
# toggle on after implementation of headless." Rationale: while the
# tool is still being iterated on and trust is being built, visible
# browsers let the user verify what's happening — and importantly,
# spot edge cases that would otherwise hide silently in headless.
# Once the workflow is stable and deployed, the user (or installer
# post-step in a future packaged release) can flip to True for
# production / mass-deploy invisibility. QSettings persists across
# updates so a single toggle in the Settings dialog sticks.
_DEFAULT_HEADLESS = False


def is_headless_mode() -> bool:
    """True → MyMaps and qchub Playwright sessions launch headless.
    False → browsers open visibly so the user can watch the automation.

    Thread-safe. Read fresh on every Playwright launch so a Settings
    change applies to the very next run without app restart.
    """
    return bool(
        QSettings(_ORG, _APP).value("headless_browsers", _DEFAULT_HEADLESS, type=bool)
    )


def set_headless_mode(headless: bool) -> None:
    """Persist the headless preference. Called from Settings dialog."""
    QSettings(_ORG, _APP).setValue("headless_browsers", bool(headless))
