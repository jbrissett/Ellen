"""First-launch setup — verify Playwright's msedge channel is ready.

When Ellen ships via the installer, the .exe doesn't bundle the
Playwright browser binaries (saves ~150MB on the installer). Instead,
on first launch we ensure `playwright install msedge` has run at
least once. Marker file at `%LOCALAPPDATA%/TrafficIntake/.playwright_setup`
records that it's done; subsequent launches skip the check entirely.

For the msedge channel specifically, "install" is fast — Edge ships
with Windows 11, so Playwright only needs to register it + download
its driver. Typical first-launch time is 5-30s, not the multi-minute
download some channels need.

If the user's machine doesn't have Edge at all (rare on Win 11), the
install will fail. We surface a clean error in that case.

This module is import-light so it can run inside the splash before
the heavy MainWindow construction. The actual install runs in a Qt
worker thread; this module exposes the worker class.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from . import config


def _marker_path() -> Path:
    return config.app_data_dir() / ".playwright_setup"


def playwright_browser_ready() -> bool:
    """True if first-launch setup has already run.

    Fast — just file existence check. Subsequent launches skip the
    full install attempt and proceed directly to MainWindow.
    """
    return _marker_path().exists()


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

    The actual subprocess: `python -m playwright install msedge`. When
    we're frozen by PyInstaller, `sys.executable` is the bundled .exe;
    we still invoke playwright as a module of THIS interpreter, which
    works because PyInstaller bundles playwright + its CLI entry point.
    """

    def __init__(self) -> None:
        super().__init__()
        self.signals = PlaywrightInstallSignals()

    def run(self) -> None:
        try:
            self.signals.progress.emit(
                "Setting up browser support (one-time, usually ~10-30 seconds)…"
            )
            # `-u` for unbuffered stdout so we can stream progress lines
            # back to the splash if Playwright emits any.
            cmd = [sys.executable, "-u", "-m", "playwright", "install", "msedge"]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=(
                    0x08000000  # CREATE_NO_WINDOW — don't pop a console
                    if sys.platform == "win32" else 0
                ),
            )
            # Stream output so the splash can show something living
            # rather than a static "setting up…" string.
            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = raw.strip()
                    if line:
                        # Trim to first ~80 chars so it fits the splash.
                        self.signals.progress.emit(line[:80])
            rc = proc.wait()
            if rc != 0:
                self.signals.failed.emit(
                    f"playwright install msedge exited with code {rc}. "
                    "If Microsoft Edge isn't installed on this machine, "
                    "install it from https://www.microsoft.com/edge and "
                    "re-launch Ellen."
                )
                return
            mark_playwright_browser_ready()
            self.signals.finished.emit()
        except FileNotFoundError as exc:
            # sys.executable missing — should be impossible from a
            # PyInstaller bundle but handle it gracefully.
            self.signals.failed.emit(
                f"Couldn't run the installer subprocess: {exc}"
            )
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
