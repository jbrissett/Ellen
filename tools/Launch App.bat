@echo off
REM Launches the Traffic Intake desktop app using the project's venv Python.

set "VENV_PY=%~dp0..\.venv\Scripts\pythonw.exe"

if not exist "%VENV_PY%" (
    echo Python venv not found at %VENV_PY%
    echo Please run setup first.
    pause
    exit /b 1
)

start "" "%VENV_PY%" -m traffic_intake.ui
