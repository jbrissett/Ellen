"""Config + credential storage.

Resolution order for shared API keys (Anthropic / Google / HERE):
  1. Environment variable (dev override)
  2. OS keyring entry (legacy override from when these were
     user-editable in Settings)
  3. Baked default — shipped with the installer via the gitignored
     `_baked_keys.py` module the build process generates

User-specific credentials (qchub login, Google MyMaps email) only
ever come from the keyring — no shared baked defaults.

The baked-key module is loaded with `try / except ImportError` so the
dev checkout (which has no `_baked_keys.py`) keeps working as long as
env vars or keyring entries are set.
"""
from __future__ import annotations

import os
from pathlib import Path

import keyring

SERVICE = "traffic-intake"
ANTHROPIC_KEY = "anthropic_api_key"
GOOGLE_GEOCODING_KEY = "google_geocoding_api_key"
HERE_API_KEY = "here_api_key"
QCHUB_USER_KEY = "qchub_username"
QCHUB_PASS_KEY = "qchub_password"
GOOGLE_EMAIL_KEY = "google_account_email"


# Baked keys, loaded from the gitignored _baked_keys module that the
# installer build script generates. Dev checkouts won't have the file —
# we fall back to None and require env / keyring instead.
try:
    from . import _baked_keys  # type: ignore[attr-defined]
    _BAKED_ANTHROPIC = getattr(_baked_keys, "ANTHROPIC", None)
    _BAKED_GOOGLE_GEOCODING = getattr(_baked_keys, "GOOGLE_GEOCODING", None)
    _BAKED_HERE = getattr(_baked_keys, "HERE", None)
except ImportError:
    _BAKED_ANTHROPIC = None
    _BAKED_GOOGLE_GEOCODING = None
    _BAKED_HERE = None


def get_api_key() -> str:
    """Read Anthropic API key. Env var > keyring (legacy override) > baked.
    Raises if none are set.
    """
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return env
    stored = keyring.get_password(SERVICE, ANTHROPIC_KEY)
    if stored:
        return stored
    if _BAKED_ANTHROPIC:
        return _BAKED_ANTHROPIC
    raise RuntimeError(
        "No Anthropic API key found. The installer should ship a baked key — "
        "if you're running from a dev checkout, set ANTHROPIC_API_KEY env var."
    )


def set_api_key(key: str) -> None:
    keyring.set_password(SERVICE, ANTHROPIC_KEY, key)


def get_google_geocoding_key() -> str | None:
    """Env var > keyring (legacy) > baked. None if all three are unset."""
    env = os.environ.get("GOOGLE_GEOCODING_API_KEY")
    if env:
        return env
    stored = keyring.get_password(SERVICE, GOOGLE_GEOCODING_KEY)
    if stored:
        return stored
    return _BAKED_GOOGLE_GEOCODING


def set_google_geocoding_key(key: str) -> None:
    keyring.set_password(SERVICE, GOOGLE_GEOCODING_KEY, key)


def get_here_api_key() -> str | None:
    """Env var > keyring (legacy) > baked. None if all three are unset.

    Optional — if not set, the geocoder logs a single warning and falls
    through to other tiers.
    """
    env = os.environ.get("HERE_API_KEY")
    if env:
        return env
    stored = keyring.get_password(SERVICE, HERE_API_KEY)
    if stored:
        return stored
    return _BAKED_HERE


def set_here_api_key(key: str) -> None:
    keyring.set_password(SERVICE, HERE_API_KEY, key)


def get_qchub_credentials() -> tuple[str, str] | None:
    user = keyring.get_password(SERVICE, QCHUB_USER_KEY)
    pw = keyring.get_password(SERVICE, QCHUB_PASS_KEY)
    if user and pw:
        return user, pw
    return None


def set_qchub_credentials(username: str, password: str) -> None:
    keyring.set_password(SERVICE, QCHUB_USER_KEY, username)
    keyring.set_password(SERVICE, QCHUB_PASS_KEY, password)


def get_google_mymaps_email() -> str | None:
    """Email of the Google account paired with the MyMaps browser profile.
    Set after a successful "Sign in to Google" run from Settings.
    """
    return keyring.get_password(SERVICE, GOOGLE_EMAIL_KEY)


def set_google_mymaps_email(email: str) -> None:
    keyring.set_password(SERVICE, GOOGLE_EMAIL_KEY, email)


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    p = Path(base) / "TrafficIntake"
    p.mkdir(parents=True, exist_ok=True)
    return p


def downloads_dir() -> Path:
    """User's standard Downloads folder. Falls back to ~/Downloads.

    On Windows, the canonical way to resolve a redirected Downloads folder
    is via SHGetKnownFolderPath, but the registry-based fallback covers
    99% of real installs. We try the registry first, then ~/Downloads,
    then ensure the directory exists.
    """
    home_default = Path.home() / "Downloads"
    try:
        import winreg  # type: ignore[import-untyped]
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        try:
            val, _ = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")
            resolved = Path(os.path.expandvars(val))
            if resolved.exists():
                return resolved
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass
    home_default.mkdir(parents=True, exist_ok=True)
    return home_default
