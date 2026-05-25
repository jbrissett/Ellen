@echo off
REM Launches the API-key popup using the project's venv Python.
REM Double-click this file from Explorer — no terminal knowledge needed.

set "VENV_PY=%~dp0..\.venv\Scripts\pythonw.exe"
set "SCRIPT=%~dp0save_api_key.py"

if not exist "%VENV_PY%" (
    echo Python venv not found at %VENV_PY%
    echo Please run setup first.
    pause
    exit /b 1
)

start "" "%VENV_PY%" "%SCRIPT%"
