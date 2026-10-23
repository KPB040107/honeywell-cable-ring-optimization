@echo off
title ResPlan Co-Design Studio
echo ===================================================
echo Starting ResPlan Co-Design Studio
echo URL: http://127.0.0.1:8000
echo ===================================================

cd /d "%~dp0"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m resplan_router.cli --serve --port 8000
) else (
    python -m resplan_router.cli --serve --port 8000
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo An error occurred while running the studio.
    pause
)
