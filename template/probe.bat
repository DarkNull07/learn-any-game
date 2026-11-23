@echo off
rem Live probing tools (see probe.py). Example:  probe.bat test
cd /d "%~dp0"
set "PY=@@PYTHON@@"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" probe.py %*
