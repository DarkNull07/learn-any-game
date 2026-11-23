@echo off
rem Starts this game's bot. Examples:  run.bat --levels 20     run.bat --levels 3 --dry-run
cd /d "%~dp0"
set "PY=@@PYTHON@@"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" bot.py %*
