@echo off
REM Double-click launcher: starts the overlay with no console window and returns
REM immediately, so nothing is left sitting in the background
setlocal
set "HERE=%~dp0"
set "PYTHONPATH=%HERE%src"
set "PYTHONIOENCODING=utf-8"

if not exist "%HERE%.venv\Scripts\pythonw.exe" (
    echo No virtual environment found.
    echo Run setup.ps1 first:
    echo     powershell -ExecutionPolicy Bypass -File "%HERE%setup.ps1"
    pause
    exit /b 1
)

start "" "%HERE%.venv\Scripts\pythonw.exe" -m livetranslator %*
endlocal
