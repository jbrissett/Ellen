"""Bootstrap entry point.

Order matters in this file:
  1. Minimal Qt imports + QApplication.
  2. Show splash IMMEDIATELY so the user has feedback within ~200ms of
     double-click. Otherwise the multi-second cost of PySide6 + anthropic
     + playwright + chat/qchub module imports is a black hole, and the
     user clicks again (corrupting the Edge profile, see
     project_single_instance_and_splash.md).
  3. Single-instance check. If another copy is running, hand off our
     argv to it and exit. Otherwise claim the lock.
  4. Heavy imports (`.app`) run with the splash visible. Splash messages
     update so the user sees what phase startup is in.
  5. Build the main window, wire the single-instance handler so a
     future second-launch attempt raises this window instead of
     spawning a duplicate.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from .single_instance import (
    claim_single_instance,
    install_secondary_launch_handler,
    instance_key,
    send_to_running_instance,
)


# Brand palette sampled from the QC logo (QC New Logo W Tag, 2026-05-18).
# Used across the splash + (future) app chrome so the look is consistent
# with print + marketing assets.
QC_NAVY    = QColor(26, 37, 64)     # ~#1A2540 — Q-letter dark navy/charcoal
QC_BLUE    = QColor(27, 71, 205)    # ~#1B47CD — C-letter royal blue
QC_INK     = QColor(26, 26, 26)     # ~#1A1A1A — tagline / wordmark black
QC_PAPER   = QColor(252, 252, 253)  # near-white panel
QC_DUSK    = QColor(110, 110, 122)  # status-line gray


# Logo asset shipped next to this file. Loaded lazily so the splash code
# stays cheap to import (matters during bootstrap — we want to paint
# before pulling in the rest of the app).
_LOGO_PATH = Path(__file__).parent / "assets" / "qc_logo.png"

# Application icon — used by Windows for the taskbar entry, alt-tab,
# and the .lnk shortcut Inno Setup creates at install time. Built as a
# multi-resolution QIcon so Windows picks the crispest size for the
# context. All sizes generated programmatically and committed in
# `assets/ellen_icon*.png` — see the icon-generation snippet in
# commit history if you need to regenerate.
_ICON_DIR = Path(__file__).parent / "assets"
_ICON_PATHS = [
    _ICON_DIR / "ellen_icon.png",       # 256 master
    _ICON_DIR / "ellen_icon_128.png",
    _ICON_DIR / "ellen_icon_64.png",
    _ICON_DIR / "ellen_icon_48.png",
    _ICON_DIR / "ellen_icon_32.png",
    _ICON_DIR / "ellen_icon_16.png",
]


def _load_app_icon() -> QIcon:
    """Return a multi-resolution QIcon for the Ellen app. Falls back to
    an empty QIcon if the asset directory is missing (distribution
    builds that dropped the assets — taskbar will use Qt's default).
    """
    icon = QIcon()
    any_loaded = False
    for p in _ICON_PATHS:
        if p.exists():
            icon.addFile(str(p))
            any_loaded = True
    return icon if any_loaded else QIcon()


def _build_splash_pixmap() -> QPixmap:
    """Splash featuring the product name 'Ellen — the workplace assistant'
    prominently, with the QC logo present but subordinate at the bottom.
    Painted on a near-white panel with a thin brand-blue accent.

    Visual hierarchy (per user direction 2026-05-21):
      1. 'Ellen' wordmark — large, brand-blue, the focal point.
      2. 'the workplace assistant' tagline — smaller, navy, under it.
      3. QC logo — small, bottom area, subordinate.

    Falls back to a text-only layout if the logo asset is missing
    (distribution build that dropped the asset) so the user still
    sees feedback within ~200ms of double-click.
    """
    width, height = 460, 280
    pm = QPixmap(width, height)
    pm.fill(QC_PAPER)
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Thin brand-blue border accent.
        painter.setPen(QC_BLUE)
        painter.drawRect(0, 0, width - 1, height - 1)

        # --- Primary: 'Ellen' wordmark (large, brand blue) ---
        wordmark_font = QFont("Segoe UI", 56)
        wordmark_font.setBold(True)
        wordmark_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 102)
        painter.setFont(wordmark_font)
        painter.setPen(QC_BLUE)
        painter.drawText(
            pm.rect().adjusted(0, 55, 0, 0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "Ellen",
        )

        # --- Secondary: tagline (smaller, navy ink, normal weight) ---
        tagline_font = QFont("Segoe UI", 14)
        tagline_font.setItalic(True)
        painter.setFont(tagline_font)
        painter.setPen(QC_INK)
        painter.drawText(
            pm.rect().adjusted(0, 145, 0, 0),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "the workplace assistant",
        )

        # --- Subordinate: small QC logo, bottom-centered ---
        logo_drawn = False
        if _LOGO_PATH.exists():
            logo = QPixmap(str(_LOGO_PATH))
            if not logo.isNull():
                target_h = 50  # was 150; now subordinate per user direction
                scaled = logo.scaledToHeight(
                    target_h,
                    Qt.TransformationMode.SmoothTransformation,
                )
                # Bottom-center, ~25px from the bottom edge.
                x = (width - scaled.width()) // 2
                y = height - scaled.height() - 25
                painter.drawPixmap(x, y, scaled)
                logo_drawn = True

        if not logo_drawn:
            # Fallback: small Quality Counts text in place of the logo.
            painter.setPen(QC_DUSK)
            credit_font = QFont("Segoe UI", 10)
            credit_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
            painter.setFont(credit_font)
            painter.drawText(
                pm.rect().adjusted(0, height - 40, 0, 0),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                "QUALITY COUNTS",
            )
    finally:
        painter.end()
    return pm


def _set_status(splash: QSplashScreen, msg: str) -> None:
    splash.showMessage(
        msg,
        Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
        QC_DUSK,
    )
    QApplication.processEvents()


def _open_path_if_email(arg: str, window) -> bool:
    """If `arg` is an existing .eml/.msg path, hand it to the window's
    drop handler. Returns True if processed.
    """
    if not arg:
        return False
    p = Path(arg)
    if not p.exists():
        return False
    if p.suffix.lower() not in (".eml", ".msg"):
        return False
    window.on_file_dropped(p)
    return True


def _claim_distinct_taskbar_identity() -> None:
    """Tell Windows we're a distinct app (not just `python.exe`) so the
    taskbar shows OUR window icon instead of the Python launcher's
    icon, and so our window doesn't get grouped with other Python apps.

    Windows uses the AppUserModelID (AUMID) to identify a "distinct"
    application for taskbar pinning, jump-lists, and icon resolution.
    When a Python script runs via python.exe, Windows defaults to the
    interpreter's AUMID — so every Python app shares the python.exe
    icon and groups together in the taskbar.

    `SetCurrentProcessExplicitAppUserModelID` overrides that for THIS
    process. Must be called BEFORE the first window is created.

    No-op on non-Windows, and swallows errors silently so a missing
    shell32 export (very old Win or unusual environment) doesn't
    block startup. Once we ship as a PyInstaller .exe this becomes
    redundant — the .exe has its own AUMID + embedded icon — but
    it's harmless to leave in.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # Until we ship a Start Menu shortcut (via the v1 installer)
        # that maps a reverse-DNS-style AUMID to a friendly display
        # name, Windows shows the RAW AUMID string in toast headers.
        # User observed this 2026-05-26: toasts showed
        # "QualityCounts.Ellen.WorkplaceAssistant.1" as the app label.
        # Use a clean short identifier instead so toasts read just
        # "Ellen". The installer can swap back to a properly-namespaced
        # AUMID + shortcut once it's built.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Ellen")
    except Exception:
        pass


def main() -> int:
    # 1. Claim a distinct Windows AppUserModelID BEFORE QApplication
    # constructs (and thus before any window appears) so the taskbar
    # uses our app icon instead of python.exe's.
    _claim_distinct_taskbar_identity()

    # 2. QApplication + splash (no heavy imports yet).
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Ellen")
    app.setOrganizationName("Quality Counts")
    # Set the app-level window icon BEFORE the splash + main window
    # are created so the taskbar entry, alt-tab thumbnail, and any
    # popups inherit it. Same icon will be baked into the .lnk
    # shortcut by Inno Setup at install time.
    app.setWindowIcon(_load_app_icon())

    splash = QSplashScreen(
        _build_splash_pixmap(),
        Qt.WindowType.WindowStaysOnTopHint,
    )
    splash.show()
    _set_status(splash, "Loading…")

    # 3. Single-instance enforcement.
    key = instance_key()
    server = claim_single_instance(key)
    if server is None:
        _set_status(splash, "Another copy is already running — bringing it forward…")
        incoming = [a for a in sys.argv[1:] if a]
        send_to_running_instance(key, incoming)
        # Give the user a moment to see the message; then exit cleanly.
        QApplication.processEvents()
        splash.close()
        return 0

    # 4. Heavy imports — splash stays visible.
    _set_status(splash, "Loading core modules…")
    from .app import MainWindow

    # 4b. First-launch browser setup — `playwright install msedge`
    # only runs once. Marker file at %LOCALAPPDATA%/TrafficIntake/
    # .playwright_setup tracks completion. Skipped on every subsequent
    # launch (fast file-existence check). When the installer ships
    # Ellen, the .exe doesn't carry the browser binaries — this is
    # the moment they get prepared.
    from ..first_launch import (
        playwright_browser_ready,
        run_first_launch_setup_blocking,
    )
    if not playwright_browser_ready():
        ok, err = run_first_launch_setup_blocking(
            lambda msg: _set_status(splash, msg),
        )
        if not ok:
            splash.close()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "Ellen — browser setup failed",
                f"{err}\n\nEllen can't continue without browser support. "
                "Resolve the issue above and re-launch.",
            )
            return 1

    _set_status(splash, "Building main window…")
    window = MainWindow()

    # 5. Wire single-instance handoff: a future second-launch attempt
    # raises THIS window and processes any .eml argument it sent.
    def _on_secondary_launch(args: list[str]) -> None:
        # Raise + activate the existing window.
        try:
            window.setWindowState(
                (window.windowState() & ~Qt.WindowState.WindowMinimized)
                | Qt.WindowState.WindowActive
            )
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception:
            pass
        # Process any file args (first valid email wins).
        for a in args:
            if _open_path_if_email(a, window):
                break

    handler = install_secondary_launch_handler(server, _on_secondary_launch)  # noqa: F841 (kept alive by ref)

    # 6. If we were launched WITH an email path, process it now.
    initial_args = [a for a in sys.argv[1:] if a]
    for a in initial_args:
        if _open_path_if_email(a, window):
            break

    _set_status(splash, "Ready.")
    window.show()
    splash.finish(window)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
