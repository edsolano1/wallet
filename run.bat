@echo off
REM Starts the finance app on this computer only.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
