@echo off
REM ===========================================================================
REM  Command Center - Windows launcher
REM  Creates a local virtual environment, installs dependencies and starts the
REM  dashboard in your default browser.  Extra arguments are passed to main.py:
REM      start.bat --no-browser --port 9000
REM ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Command Center
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo.
echo   ==============================================
echo     COMMAND CENTER  -  local PC dashboard
echo   ==============================================
echo.

REM ------------------------------------------------------------------ Python
set "PYTHON="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON=py -3"
if not defined PYTHON (
  where python >nul 2>nul
  if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
  echo   [X] Python was not found.
  echo.
  echo   Install Python 3.10 or newer from https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

%PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo   [X] Python 3.10 or newer is required.
  %PYTHON% --version
  echo     Download it from https://www.python.org/downloads/
  pause
  exit /b 1
)

REM --------------------------------------------------------------- virtualenv
set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo   [1/3] Creating a local virtual environment...
  %PYTHON% -m venv "%VENV%"
  if errorlevel 1 (
    echo   [!] Could not create the virtual environment. Falling back to the system Python.
    set "VENV="
  )
)

REM RUNPY is intentionally unquoted at the call sites: it already carries its
REM own quotes when it points at the venv interpreter.
if defined VENV (
  set RUNPY="%CD%\%VENV%\Scripts\python.exe"
) else (
  set "RUNPY=%PYTHON%"
)

REM -------------------------------------------------------------- dependencies
echo   [2/3] Installing dependencies...
%RUNPY% -m pip install --upgrade pip --quiet --disable-pip-version-check >nul 2>nul
%RUNPY% -m pip install -r "backend\requirements.txt" --quiet --disable-pip-version-check
if errorlevel 1 (
  echo.
  echo   [!] Some dependencies failed to install. Retrying with full output...
  echo.
  %RUNPY% -m pip install -r "backend\requirements.txt"
  if errorlevel 1 (
    echo.
    echo   [X] Could not install the required packages.
    echo       Check your internet connection, then run start.bat again.
    pause
    exit /b 1
  )
)

REM -------------------------------------------------------------------- start
echo   [3/3] Starting the dashboard...
echo.
cd backend
%RUNPY% main.py %*
set "EXITCODE=%ERRORLEVEL%"
cd ..
if not "%EXITCODE%"=="0" (
  echo.
  echo   The dashboard stopped with exit code %EXITCODE%.
  echo   Logs are in the "logs" folder.
  pause
)
endlocal & exit /b %EXITCODE%
