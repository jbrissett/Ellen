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


# Default for the headless toggle. False = browsers VISIBLE (dev /
# diagnostics) — appropriate for the in-development build the user is
# running today. The installer's post-install step will write
# `headless_browsers=True` so the production install for QC staff opens
# Ellen with browsers invisible out of the box.
#
# Centralized here so flipping the default at installer-build time is
# a one-line change to the install script (writes the registry value
# under HKCU\Software\Quality Counts\Traffic Intake) — no Python rebuild.
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
