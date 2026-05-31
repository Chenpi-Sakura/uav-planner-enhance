@echo off
title UAV Planner Startup
echo ===================================================
echo     Starting UAV 4D Weather-Aware Planner...
echo ===================================================
echo.

echo [1/2] Starting Backend Server (FastAPI)...
start "UAV Backend" cmd /k "cd backend && call venv\Scripts\activate.bat && uvicorn main:app --reload --host 0.0.0.0 --port 8000"

echo [2/2] Starting Frontend Server (Vue3)...
start "UAV Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo Both services are starting in separate windows.
echo - Backend will be available at: http://localhost:8000
echo - Frontend will be available at: http://localhost:5173
echo.
echo You can close this window now. The servers will keep running in their own windows.
pause
