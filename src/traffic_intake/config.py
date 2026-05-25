"""Config + credential storage. API key lives in Windows Credential Manager via keyring."""
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


def get_api_key() -> str:
    """Read Anthropic API key. Env var wins (for dev); falls back to OS keyring."""
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return env
    stored = keyring.get_password(SERVICE, ANTHROPIC_KEY)
    if stored:
        return stored
    raise RuntimeError(
        "No Anthropic API key found. Set ANTHROPIC_API_KEY env var, or save one via the app's settings."
    )


def set_api_key(key: str) -> None:
    keyring.set_password(SERVICE, ANTHROPIC_KEY, key)


def get_google_geocoding_key() -> str | None:
    """Read Google Geocoding API key. Env var wins; falls back to OS keyring."""
    env = os.environ.get("GOOGLE_GEOCODING_API_KEY")
    if env:
        return env
    return keyring.get_password(SERVICE, GOOGLE_GEOCODING_KEY)


def set_google_geocoding_key(key: str) -> None:
    keyring.set_password(SERVICE, GOOGLE_GEOCODING_KEY, key)


def get_here_api_key() -> str | None:
    """Read HERE Geocoding & Search API key. Env var wins; falls back to OS keyring.

    Optional — if not set, the geocoder falls through to other tiers and logs
    a single warning. Don't raise — many users may not have a HERE account
    and the chain should degrade gracefully.
    """
    env = os.environ.get("HERE_API_KEY")
    if env:
        return env
    return keyring.get_password(SERVICE, HERE_API_KEY)


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
