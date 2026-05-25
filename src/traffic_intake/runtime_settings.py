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


# Default for the headless toggle. TRUE = browsers run invisibly
# (production / mass-deploy default per user direction 2026-05-24:
# "the default setting should be invisible"). The project lead can
# untick the Settings checkbox to surface the browsers for visual
# diagnostics.
#
# Flipped from False → True 2026-05-24. Previous comment claimed the
# installer would write this at install time; making it the code-level
# default instead means a fresh install or a Settings-reset gets the
# right behavior without depending on an installer post-step.
_DEFAULT_HEADLESS = True


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
