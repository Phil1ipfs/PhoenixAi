@echo off
REM Launches PHOENIX AI (backend + built UI) on http://127.0.0.1:8000
cd /d "%~dp0backend"
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" run.py
