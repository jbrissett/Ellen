"""First-launch setup — `playwright install msedge` on the first run.

When Ellen ships via the installer, the .exe doesn't bundle the
Playwright browser binaries. Instead, on first launch we run
`playwright install msedge` once — downloads ~150MB to a per-user
writable directory, then a marker file at
`%LOCALAPPDATA%/TrafficIntake/.playwright_setup` records it's done
so subsequent launches skip the check entirely.

History (2026-05-26 → 2026-05-27):
  - v1 attempt 1: shell out to `sys.executable -m playwright install
    msedge`. Backfired because sys.executable in a PyInstaller bundle
    is Ellen.exe (not a Python interpreter), so the subprocess just
    re-launched Ellen, hit single-instance lock, handed off args, and
    exited — looping orphan instances forever.
  - v1 attempt 2 (the BAD assumption): bypass install entirely on
    the theory that the bundled `_internal/playwright/driver` was
    sufficient. Colleague hit the real error on his x64 machine:
    Playwright tried to launch chromium at
    `_internal/playwright/driver/package/.local-browsers/chromium-1223/
    chrome-win64/chrome.exe` which doesn't exist (.local-browsers is
    populated by `playwright install`, not by PyInstaller's hooks).
    msedge channel also failed without the install — its channel
    registration data lives in the same .local-browsers tree.
  - v1.0.1 (this): in-process call to `playwright.__main__.main()`
    avoids the sys.executable trap. Combined with
    PLAYWRIGHT_BROWSERS_PATH pointing at a writable per-user dir, the
    install lands in `%LOCALAPPDATA%/TrafficIntake/playwright-browsers/`
    and Playwright's runtime browser discovery finds them there.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from . import config


def playwright_browsers_dir() -> Path:
    """Per-user, writable directory where Playwright stores its
    downloaded browser binaries. We control the path via the
    PLAYWRIGHT_BROWSERS_PATH env var (see configure_playwright_env).
    """
    return config.app_data_dir() / "playwright-browsers"


def configure_playwright_env() -> None:
    """Point Playwright at a writable per-user browser directory.

    Without this, Playwright defaults to
    `<playwright_install_dir>/driver/package/.local-browsers/`, which
    inside a PyInstaller bundle resolves to a read-only path under
    `_internal/playwright/...`. Both `playwright install` and the
    runtime browser-discovery walk would fail there.

    MUST be called BEFORE any code path imports playwright — once
    playwright's drivers module reads PLAYWRIGHT_BROWSERS_PATH on
    import, it caches the value.

    On a dev checkout (sys.frozen unset) we leave the env alone so
    Playwright uses its default (~/AppData/Local/ms-playwright/),
    where the developer has presumably already run `playwright install`.
    """
    if not getattr(sys, "frozen", False):
        return
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(playwright_browsers_dir()),
    )


def _marker_path() -> Path:
    return config.app_data_dir() / ".playwright_setup"


def playwright_browser_ready() -> bool:
    """True if the first-launch install has already completed.
    Fast — just a file-existence check.
    """
    return _marker_path().exists()


def mark_playwright_browser_ready() -> None:
    """Write the marker file so future launches skip the install step."""
    try:
        _marker_path().write_text("ok\n", encoding="utf-8")
    except Exception:
        pass


def mark_playwright_browser_ready() -> None:
    """Write the marker file so future launches skip the setup step."""
    try:
        _marker_path().write_text("ok\n", encoding="utf-8")
    except Exception:
        # If we can't write the marker, the worst case is we re-run
        # the install on next launch (which is idempotent + fast).
        pass


class PlaywrightInstallSignals(QObject):
    progress = Signal(str)    # status text for the splash
    finished = Signal()       # install completed successfully
    failed = Signal(str)      # install error message


class PlaywrightInstallWorker(QRunnable):
    """Run `playwright install msedge` in a background thread so the
    splash stays responsive. Emits progress / finished / failed signals
    over `self.signals`.

    Implementation: in-process call to `playwright.__main__.main(['install',
    'msedge'])`. Bypasses sys.executable (which in a PyInstaller bundle
    is Ellen.exe, not a Python interpreter — see module-level history).
    Playwright then writes the downloaded browsers under
    PLAYWRIGHT_BROWSERS_PATH (set by configure_playwright_env() in
    __main__.py).
    """

    def __init__(self) -> None:
        super().__init__()
        self.signals = PlaywrightInstallSignals()

    def run(self) -> None:
        try:
            self.signals.progress.emit(
                "Downloading browser support (one-time, ~150MB)…"
            )
            from playwright.__main__ import main as pw_main

            # pw_main raises SystemExit on completion (mirrors a CLI
            # invocation). Catch + inspect the exit code so we can
            # distinguish success (0) from failure (nonzero) without
            # tearing down the worker thread.
            rc = 0
            try:
                pw_main(["install", "msedge"])
            except SystemExit as se:
                rc = int(se.code or 0)

            if rc != 0:
                self.signals.failed.emit(
                    f"playwright install msedge exited with code {rc}. "
                    "If Microsoft Edge isn't installed on this machine, "
                    "install it from https://www.microsoft.com/edge and "
                    "re-launch Ellen. If Edge IS installed, check your "
                    "internet connection — Playwright downloads ~150MB "
                    "of browser components from a Microsoft CDN."
                )
                return
            mark_playwright_browser_ready()
            self.signals.finished.emit()
        except Exception as exc:
            self.signals.failed.emit(
                f"Unexpected error during browser setup: "
                f"{type(exc).__name__}: {exc}"
            )


def run_first_launch_setup_blocking(
    splash_status_setter: Callable[[str], None],
) -> tuple[bool, str]:
    """Synchronously block until the install finishes, pumping Qt events
    so the splash stays responsive. Returns (ok, error_message).

    Used from `__main__.py`'s splash sequence — the install must
    complete before MainWindow constructs (which imports Playwright-
    using modules indirectly through mymaps.py / qchub.py).
    """
    from PySide6.QtCore import QEventLoop, QThreadPool
    from PySide6.QtWidgets import QApplication

    worker = PlaywrightInstallWorker()
    loop = QEventLoop()
    result: dict = {"ok": False, "error": ""}

    def _on_progress(msg: str) -> None:
        splash_status_setter(msg)
        QApplication.processEvents()

    def _on_finished() -> None:
        result["ok"] = True
        loop.quit()

    def _on_failed(msg: str) -> None:
        result["ok"] = False
        result["error"] = msg
        loop.quit()

    worker.signals.progress.connect(_on_progress)
    worker.signals.finished.connect(_on_finished)
    worker.signals.failed.connect(_on_failed)
    QThreadPool.globalInstance().start(worker)
    loop.exec()  # blocks until the worker emits finished or failed
    return result["ok"], result["error"]
