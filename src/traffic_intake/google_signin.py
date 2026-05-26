"""Helper for the Settings "Sign in to Google for MyMaps" button.

Launches a visible Edge browser pointed at Google's sign-in page,
sharing the SAME user-data directory MyMaps automation uses. After the
user signs in and closes the browser, the Google session cookies
persist in that profile and every subsequent MyMaps run picks them up
automatically — headless or visible.

Why not Playwright: the sign-in flow may involve CAPTCHA, 2FA, "verify
it's you" device prompts, etc. — all of which Playwright either can't
handle or handles awkwardly. Driving Edge directly via the OS keeps it
unsurprising: the user sees their normal browser, signs in normally,
closes it normally.

Edge executable resolution: try a few standard install paths. If we
can't find Edge, surface a clean error so the user can install it (or
we can extend with browser-channel selection later).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .config import app_data_dir


# Reuse the SAME profile dir MyMaps automation uses, so sign-in cookies
# carry over into every Playwright launch. mymaps.py launches with this
# exact path; keep them in sync.
_MYMAPS_PROFILE_DIR = app_data_dir() / "browser-profile"

# Common Edge install locations on Windows. Tried in order; first hit
# wins. If none match, _find_edge_executable returns None and the
# caller surfaces a "couldn't find Edge" error.
_EDGE_CANDIDATES = [
    r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
    r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
]


def _find_edge_executable() -> Optional[Path]:
    """Locate msedge.exe on this Windows machine. None if missing."""
    if sys.platform != "win32":
        return None
    for raw in _EDGE_CANDIDATES:
        expanded = Path(os.path.expandvars(raw))
        if expanded.exists():
            return expanded
    return None


def launch_google_signin() -> tuple[bool, str]:
    """Open Edge pointed at the Google sign-in page with the MyMaps
    profile. Returns (ok, message).

    The browser launch is fire-and-forget — we don't wait for it to
    close or try to detect a successful sign-in. The user signs in,
    closes the browser when done, and the cookies persist in the
    profile for future Playwright runs.

    Errors are returned (not raised) so the caller can surface a
    friendly message in the Settings dialog without try/except dance.
    """
    if sys.platform != "win32":
        return False, "Google sign-in launcher is Windows-only today."

    edge = _find_edge_executable()
    if edge is None:
        return False, (
            "Couldn't find Microsoft Edge. Install Edge or, if it's "
            "installed in an unusual location, set a shortcut to the "
            "msedge.exe binary and reach out for support."
        )

    _MYMAPS_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # subprocess.Popen so we don't block the Settings dialog —
        # browser opens in the background, user interacts with it
        # normally, dialog stays responsive.
        subprocess.Popen(
            [
                str(edge),
                f"--user-data-dir={_MYMAPS_PROFILE_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://accounts.google.com/",
            ],
            close_fds=True,
        )
    except Exception as exc:
        return False, f"Couldn't launch Edge: {type(exc).__name__}: {exc}"

    return True, (
        "Edge opened to Google sign-in. Sign in, then close the browser. "
        "Future MyMaps runs will use this saved session."
    )
