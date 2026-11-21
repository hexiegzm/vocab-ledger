@echo off
rem ============================================================
rem  vocab-ledger - Windows launcher (double-click me)
rem  Opens the study page in your browser, no console window.
rem  Requires Python 3.10+ with python / pythonw on PATH.
rem
rem  NOTE: keep this file ASCII-only. cmd.exe parses it before
rem  "chcp 65001" takes effect, so non-ASCII here breaks the
rem  script. The Chinese help text lives in msg\python-missing.txt
rem  and is printed with "type" (safe: it is data, not script).
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist "vocab.py" (
  echo [vocab-ledger] vocab.py not found.
  echo Put this .cmd in the repository root.
  pause
  exit /b 1
)

rem --- pick an interpreter, preferring the windowless one ---
set "PYW="
where pythonw.exe >nul 2>nul && set "PYW=pythonw"
if not defined PYW (
  where python.exe >nul 2>nul && set "PYW=python"
)
if not defined PYW goto :nopython

rem --- prove it is a real interpreter ---
rem The Microsoft Store "python.exe" is only an app-execution alias:
rem "where" finds it, but running it fails with errorlevel 9009.
"%PYW%" -c "import sys" >nul 2>nul
if errorlevel 1 goto :nopython

rem --- version check: need 3.10+ ---
"%PYW%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto :oldpython

start "" "%PYW%" vocab.py app --open
exit /b 0

:nopython
echo.
if exist "msg\python-missing.txt" (
  type "msg\python-missing.txt"
) else (
  echo [vocab-ledger] Python not found. Install Python 3.10+ from python.org
  echo and tick "Add python.exe to PATH", then run this file again.
)
echo.
pause
exit /b 1

:oldpython
echo.
echo [vocab-ledger] Python found, but it is too old.
echo Need 3.10 or newer. Current version:
"%PYW%" -V
echo.
echo Please install a newer Python from https://www.python.org/downloads/
echo.
pause
exit /b 1
