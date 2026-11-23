@echo off
rem gamekit: learn any phone game from one recorded session. Run "gamekit help" for the steps.
set "GK=%~dp0"
set "PY=python"
if exist "%GK%.venv\Scripts\python.exe" set "PY=%GK%.venv\Scripts\python.exe"
"%PY%" "%GK%gamekit.py" %*
