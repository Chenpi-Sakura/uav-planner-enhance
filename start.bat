@echo off
title UAV Planner Startup
echo ===================================================
echo     Starting UAV 4D Weather-Aware Planner...
echo ===================================================
echo.

echo [1/2] Starting Backend Server (FastAPI)...
start "UAV Backend" cmd /k "cd /d backend && .python\portable\python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"

echo [2/2] Starting Frontend Server (Vue3)...
start "UAV Frontend" cmd /k "cd /d frontend && npm run dev"

echo.
echo Both services are starting in separate windows.
echo - Backend will be available at: http://localhost:8000
echo - Frontend will be available at: http://localhost:5173
echo.
echo Waiting 5 seconds before opening browser...
timeout /t 5 /nobreak >nul

echo Opening Frontend in default browser...
start http://localhost:5173

echo.
echo Done! The browser has been opened to the frontend.
echo You can close this window now. The servers will keep running in their own windows.
pause