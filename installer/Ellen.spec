# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Ellen — produces `dist/Ellen/Ellen.exe` (one-folder).

Inno Setup wraps the entire `dist/Ellen/` folder into a single installer
.exe that drops it into `%LOCALAPPDATA%/Ellen/`. End users see one
shortcut named "Ellen" pointing at `Ellen.exe`; the sidecar files
(.dll, .pyd, asset folders) live next to the .exe and are invisible
to the user.

One-folder vs one-file: one-folder starts ~3-5× faster (no temp-dir
unpack on every launch). The trade-off is a directory of files instead
of a single .exe — Inno Setup hides this from the user.

Run via the build orchestrator: tools\build_installer.bat, which
ensures _baked_keys.py + ellen.ico exist before calling PyInstaller.
Don't invoke this spec directly without the orchestrator.
"""
from pathlib import Path

# Spec files run with PyInstaller's variables in scope, including the
# implicit `Analysis`, `PYZ`, `EXE`, `COLLECT` and the `block_cipher`
# constant — all defined by PyInstaller at runtime when this spec
# executes. The vars below are added by us.

block_cipher = None

REPO = Path(SPECPATH).parent  # SPECPATH = the directory containing this .spec
SRC = REPO / "src"
ASSETS = SRC / "traffic_intake" / "ui" / "assets"
ICON = REPO / "installer" / "ellen.ico"

# Hidden imports PyInstaller's static analyzer misses. Keep the list
# tight — every entry is something we've actually observed needing on
# this stack.
HIDDEN_IMPORTS = [
    # keyring's Windows backend is loaded lazily by name; static analysis
    # can't see the import string, so PyInstaller leaves it out by default.
    "keyring.backends.Windows",
    "keyring.backends.fail",
    # Playwright's sync API path — touched by mymaps.py and qchub.py.
    "playwright.sync_api",
    "playwright._impl._driver",
    # PySide6 sub-modules used by chat panel / system tray.
    "PySide6.QtSvg",
]

# Asset bundling. Each tuple: (source path, destination dir inside the bundle).
# Paths inside the bundle are relative to the bundle root (Ellen/).
DATAS = [
    (str(ASSETS), "traffic_intake/ui/assets"),
]

# If the build orchestrator generated _baked_keys.py, include it as a
# module file. PyInstaller treats it as a Python source like any other
# in the package, so the production build's `from . import _baked_keys`
# in config.py resolves at runtime. Dev builds without the file just
# fall back to env vars / keyring (config.py handles this).
_BAKED = SRC / "traffic_intake" / "_baked_keys.py"
if _BAKED.exists():
    DATAS.append((str(_BAKED), "traffic_intake"))


a = Analysis(
    [str(SRC / "traffic_intake" / "ui" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ellen",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX compression often trips Windows AV — skip.
    console=False,        # windowed (no console flash on launch)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Ellen",
)
