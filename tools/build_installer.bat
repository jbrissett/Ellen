@echo off
REM Build the Ellen installer end-to-end.
REM
REM Reads the three shared API keys from the local Windows Credential
REM Manager (set via `Save API Key.bat` or the old Settings dialog),
REM generates the gitignored _baked_keys.py module, runs PyInstaller,
REM then Inno Setup. Output: dist\Ellen-Setup-v1.0.0.exe
REM
REM Prerequisites (one-time):
REM   1. Venv at .venv with all runtime + dev deps installed
REM      (`.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt`)
REM   2. PyInstaller installed in the venv (`pip install pyinstaller`)
REM   3. Pillow installed in the venv (`pip install Pillow`)
REM   4. Inno Setup 6 installed at default path:
REM      C:\Program Files (x86)\Inno Setup 6\ISCC.exe
REM      (override via INNO_SETUP_ISCC env var)
REM
REM On a clean build: cleans dist/ and build/ first so old artifacts
REM don't contaminate the new build.

setlocal ENABLEDELAYEDEXPANSION

set "REPO=%~dp0.."
pushd "%REPO%"

if not exist .venv\Scripts\python.exe (
    echo ERROR: .venv not found. Set up the dev venv first.
    popd
    exit /b 1
)
set "VENV_PY=%REPO%\.venv\Scripts\python.exe"

REM 1. Read the baked keys from keyring + generate _baked_keys.py.
REM    Module is gitignored; lives only for this build's lifetime.
echo [1/5] Generating _baked_keys.py from local keyring...
"%VENV_PY%" tools\write_baked_keys.py
if errorlevel 1 (
    echo Failed to write _baked_keys.py - aborting.
    popd
    exit /b 1
)

REM 2. Build the multi-resolution .ico the installer + .exe use.
echo [2/5] Building installer\ellen.ico...
"%VENV_PY%" installer\build_ico.py
if errorlevel 1 (
    echo Failed to build ellen.ico - aborting.
    popd
    exit /b 1
)

REM 3. Clean any previous PyInstaller output so we get a clean tree.
echo [3/5] Cleaning previous build artifacts...
if exist build rmdir /s /q build
if exist dist\Ellen rmdir /s /q dist\Ellen
if exist dist\Ellen-Setup-v*.exe del /q dist\Ellen-Setup-v*.exe

REM 4. Run PyInstaller against the spec file.
echo [4/5] Running PyInstaller (this takes 1-3 minutes)...
"%VENV_PY%" -m PyInstaller --noconfirm installer\Ellen.spec
if errorlevel 1 (
    echo PyInstaller failed - aborting.
    popd
    exit /b 1
)
if not exist "dist\Ellen\Ellen.exe" (
    echo ERROR: PyInstaller succeeded but Ellen.exe is missing.
    popd
    exit /b 1
)

REM 5. Run Inno Setup against the .iss.
echo [5/5] Running Inno Setup...
set "ISCC=%INNO_SETUP_ISCC%"
if "%ISCC%"=="" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo ERROR: Inno Setup compiler not found at "%ISCC%".
    echo Install from https://jrsoftware.org/isdl.php or set INNO_SETUP_ISCC env var.
    popd
    exit /b 1
)
"%ISCC%" installer\Ellen.iss
if errorlevel 1 (
    echo Inno Setup compile failed - aborting.
    popd
    exit /b 1
)

REM Final: scrub the baked keys file from the source tree so it doesn't
REM stick around between builds. The installer .exe already has the keys
REM compiled into its bundle.
if exist src\traffic_intake\_baked_keys.py del /q src\traffic_intake\_baked_keys.py

echo.
echo ===============================================
echo  BUILD COMPLETE.
echo  Installer output: dist\Ellen-Setup-v1.0.0.exe
echo ===============================================

popd
endlocal
exit /b 0
