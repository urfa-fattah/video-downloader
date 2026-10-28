@echo off
REM Grabbit - launcher for Windows.
REM Prefers the official "py" launcher (avoids MSYS2/MinGW Python problems).
cd /d "%~dp0"

set "BASEPY="
where py >nul 2>nul
if not errorlevel 1 set "BASEPY=py -3"
if not defined BASEPY (
  where python >nul 2>nul
  if not errorlevel 1 set "BASEPY=python"
)
if not defined BASEPY (
  echo [!] Python not found. Install it with:  winget install Python.Python.3.12
  echo     or from https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
  pause
  exit /b 1
)

REM Create venv if neither layout exists (Scripts = python.org, bin = MSYS2)
if not exist ".venv\Scripts\python.exe" if not exist ".venv\bin\python.exe" (
  %BASEPY% -m venv .venv >nul 2>nul
)

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist ".venv\bin\python.exe" set "PY=.venv\bin\python.exe"
if not defined PY (
  echo [i] Virtual environment unavailable - using system Python instead.
  set "PY=%BASEPY%"
)

echo [*] Installing / updating dependencies...
%PY% -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
  echo [!] Dependency install failed. See README troubleshooting.
  pause
  exit /b 1
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [!] FFmpeg not found. HD merge, MP3 and trim need it:  winget install Gyan.FFmpeg
)

%PY% app.py
pause
