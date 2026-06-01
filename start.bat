@echo off
chcp 65001 >nul
title UAV Planner Startup
echo ===================================================
echo     Starting UAV 4D Weather-Aware Planner...
echo ===================================================
echo.

cd /d "%~dp0"

REM Resolve Python interpreter as ABSOLUTE path so it works regardless of
REM the new window's working directory set by `start /D` below.
set "PY_EXE="
if exist "backend\.python\portable\python.exe" (
    set "PY_EXE=%~dp0backend\.python\portable\python.exe"
) else (
    where python >nul 2>&1 && set "PY_EXE=python"
    if not defined PY_EXE (
        where py >nul 2>&1 && set "PY_EXE=py -3"
    )
)

if not defined PY_EXE (
    echo [X] Python not found. Please run setup.bat first.
    pause
    exit /b 1
)

echo [OK] Using Python: %PY_EXE%
echo.

REM Use start /D to set working dir for the new window.
REM Avoid embedding %~dp0 in start's quoted command (PowerShell won't expand it).

echo [1/2] Starting Backend Server (FastAPI) on port 8000...
start "UAV Backend" /D "%~dp0backend" cmd /k %PY_EXE% -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

echo [2/2] Starting Frontend Server (Vue3) on port 5173...
start "UAV Frontend" /D "%~dp0frontend" cmd /k npm run dev

echo.
echo Both services are starting in separate windows.
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:5173
echo.
echo Waiting 5 seconds before opening browser...
timeout /t 5 /nobreak >nul

echo Opening Frontend in default browser...
start http://localhost:5173

echo.
echo Done! The browser has been opened to the frontend.
echo You can close this window now. The servers will keep running in their own windows.
pause
