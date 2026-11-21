@echo off
rem ============================================================
rem  vocab-ledger - Windows launcher (double-click me)
rem  Opens the study page in your browser, no console window.
rem  Requires Python 3.10+ with python / pythonw on PATH.
rem
rem  NOTE: keep this file ASCII-only. cmd.exe parses it before
rem  "chcp 65001" takes effect, so non-ASCII comments break it.
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist "vocab.py" (
  echo [vocab-ledger] vocab.py not found. Put this file in the repo root.
  pause
  exit /b 1
)

set "PYW=pythonw"
where pythonw.exe >nul 2>nul
if errorlevel 1 set "PYW=python"

start "" "%PYW%" vocab.py app --open
exit /b 0
