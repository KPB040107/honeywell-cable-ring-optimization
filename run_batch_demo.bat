@echo off
title ResPlan Batch Router Demo
echo ===================================================
echo Running ResPlan Cable Ring Solver Demo
echo ===================================================

cd /d "%~dp0"
if not exist "%~dp0output" mkdir "%~dp0output"

set PYTHON_BIN="%~dp0.venv\Scripts\python.exe"
if not exist %PYTHON_BIN% set PYTHON_BIN=python

echo Routing Demo Floor Plan: demo_three_rooms_window.json ...
%PYTHON_BIN% -m resplan_router.cli -i tests/fixtures/demo_three_rooms_window.json -o output/demo_three_rooms_solution.json -v output/demo_three_rooms_overlay.png

echo.
echo Routing Demo Floor Plan: demo_six_rooms_capacity.json ...
%PYTHON_BIN% -m resplan_router.cli -i tests/fixtures/demo_six_rooms_capacity.json -o output/demo_six_rooms_solution.json -v output/demo_six_rooms_overlay.png

echo.
echo ===================================================
echo Done! Output files saved in output/ directory.
echo ===================================================
pause
