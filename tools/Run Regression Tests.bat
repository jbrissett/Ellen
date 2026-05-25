@echo off
REM Runs the regression test suite to catch silent breakage.
REM
REM Run this BEFORE shipping any change that touched qchub.py, models.py,
REM or chat.py — it covers the bug shapes the user has hit so far:
REM   - 1-day duration translation (00:00-23:59 must render as 24h)
REM   - Tube subtype splitting (Volume vs Volume,Class as separate groups)
REM   - Survey subtype label drift (Custom subtypes need the ellipsis)
REM   - StudyLocation survey_custom_name bidirectional rule
REM   - Layer-name format for full-day windows
REM
REM Each green run is evidence the recent change didn't regress a fix.
REM If anything goes RED, don't ship until you understand why.

set "VENV_PY=%~dp0..\.venv\Scripts\python.exe"
set "REPO_ROOT=%~dp0.."

if not exist "%VENV_PY%" (
    echo Python venv not found at %VENV_PY%
    echo Please run setup first.
    pause
    exit /b 1
)

REM Make sure pytest is installed in the venv (one-time install on first run).
"%VENV_PY%" -c "import pytest" 2>NUL
if errorlevel 1 (
    echo Installing pytest into venv (one-time^)...
    "%VENV_PY%" -m pip install pytest
    if errorlevel 1 (
        echo pytest install failed.
        pause
        exit /b 1
    )
)

echo.
echo Running Traffic Intake regression suite...
echo.

pushd "%REPO_ROOT%"
"%VENV_PY%" -m pytest tests\ -v
set "TEST_RESULT=%errorlevel%"
popd

echo.
if "%TEST_RESULT%"=="0" (
    echo ===============================================
    echo  ALL TESTS PASSED. Safe to ship the change.
    echo ===============================================
) else (
    echo ===============================================
    echo  TESTS FAILED. Review the output above.
    echo  Do NOT ship until red tests are explained.
    echo ===============================================
)
echo.
pause
exit /b %TEST_RESULT%
