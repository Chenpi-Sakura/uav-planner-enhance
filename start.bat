@echo off
chcp 65001 >nul
title UAV Planner Startup
echo ===================================================
echo     Starting UAV 4D Weather-Aware Planner...
echo ===================================================
echo.

cd /d "%~dp0"

REM Optional: `start.bat public` additionally serves the service over the frp
REM tunnel (dist watch build + frpc). Without the argument nothing is exposed.
set "PUBLIC_MODE="
if /i "%~1"=="public" set "PUBLIC_MODE=1"

REM PIDs are appended by PowerShell: a cmd `>>` redirect handle is inherited by the
REM spawned service and then locks the file, so later appends fail silently.
set "PIDFILE=%TEMP%\uav_planner_pids.txt"
if exist "%PIDFILE%" del "%PIDFILE%" >nul 2>&1

REM Backend port is read from config.json at repo root (single source of truth)
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "$c = Get-Content -Raw '%~dp0config.json' | ConvertFrom-Json; Write-Output $c.backend.port"`) do set "BACKEND_PORT=%%P"
if not defined BACKEND_PORT (
    echo [WARN] Could not read backend port from config.json, using default 9090.
    set "BACKEND_PORT=9090"
)

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

REM The StaticFiles mount is decided once when main.py is imported, so a missing
REM dist would leave the public entry serving 404 until the backend restarts.
if defined PUBLIC_MODE (
    if not exist "frontend\dist\index.html" (
        echo [public] No frontend build found, running npm run build first...
        pushd frontend
        call npm run build
        popd
    )
)

REM Use start /D to set working dir for the new window.
REM Avoid embedding %~dp0 in start's quoted command (PowerShell won't expand it).

echo [1/2] Starting Backend Server (FastAPI) on port %BACKEND_PORT%...
powershell -NoProfile -Command "$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/k','%PY_EXE% -m uvicorn main:app --reload --host 0.0.0.0 --port %BACKEND_PORT%' -WorkingDirectory '%~dp0backend' -PassThru; Set-Content -LiteralPath $env:PIDFILE -Value $p.Id"

echo [2/2] Starting Frontend Server (Vue3)...
REM Run frontend hidden with output redirected to logs so the actual Vite port can be extracted.
if exist "%TEMP%\uav_frontend.log" del "%TEMP%\uav_frontend.log" >nul 2>&1
if exist "%TEMP%\uav_frontend_err.log" del "%TEMP%\uav_frontend_err.log" >nul 2>&1
if exist "%TEMP%\uav_frontend_url.txt" del "%TEMP%\uav_frontend_url.txt" >nul 2>&1
powershell -NoProfile -Command "$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','npm run dev' -WorkingDirectory '%~dp0frontend' -RedirectStandardOutput '%TEMP%\uav_frontend.log' -RedirectStandardError '%TEMP%\uav_frontend_err.log' -WindowStyle Hidden -PassThru; Add-Content -LiteralPath $env:PIDFILE -Value $p.Id"

if not defined PUBLIC_MODE goto :skip_public

echo.
echo [public] Keeping frontend/dist up to date (npm run build:watch)...
powershell -NoProfile -Command "$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','npm run build:watch' -WorkingDirectory '%~dp0frontend' -RedirectStandardOutput '%TEMP%\uav_watch.log' -RedirectStandardError '%TEMP%\uav_watch_err.log' -WindowStyle Hidden -PassThru; Add-Content -LiteralPath $env:PIDFILE -Value $p.Id"

if not exist "%~dp0frp\frpc.exe" (
    echo [public][WARN] frp\frpc.exe not found, tunnel not started. See docs\公网访问部署.md
) else (
    echo [public] Starting frp tunnel - the service is now reachable from the internet...
    powershell -NoProfile -Command "$p = Start-Process -FilePath '%~dp0frp\frpc.exe' -ArgumentList '-c','frpc.toml' -WorkingDirectory '%~dp0frp' -RedirectStandardOutput '%TEMP%\uav_frpc.log' -RedirectStandardError '%TEMP%\uav_frpc_err.log' -WindowStyle Hidden -PassThru; Add-Content -LiteralPath $env:PIDFILE -Value $p.Id"
)

:skip_public

echo.
echo Both services are starting in separate windows.
echo   Backend:  http://localhost:%BACKEND_PORT%
echo.
echo Waiting for frontend to report its address...
REM Extract the actual Vite Local URL from the frontend log (port may shift when 5173 is taken).
powershell -NoProfile -Command "$url=$null; for($i=0;$i -lt 40 -and -not $url;$i++){ Start-Sleep -Milliseconds 500; foreach($log in @('%TEMP%\uav_frontend.log','%TEMP%\uav_frontend_err.log')){ if(-not $url -and (Test-Path $log)){ $lines=Get-Content $log -ErrorAction SilentlyContinue; foreach($ln in $lines){ if(-not $url){ $clean=$ln -replace '\x1b\[[0-9;]*m',''; if($clean -match 'Local:'){ if($clean -match 'http://[^\s]+'){ $url=$matches[0] } } } } } } }; if($url){ Write-Output $url } else { Write-Output 'http://localhost:5173' }" > "%TEMP%\uav_frontend_url.txt"
set /p FRONTEND_URL=<"%TEMP%\uav_frontend_url.txt"

echo   Frontend: %FRONTEND_URL%
if defined PUBLIC_MODE echo   Tunnel:   frpc log at %TEMP%\uav_frpc.log
echo.
echo Opening Frontend in default browser...
start "" "%FRONTEND_URL%"

REM Watch the start window: closing it shuts down the backend and frontend services.
start "" /min powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\start_watcher.ps1" -PidFile "%TEMP%\uav_planner_pids.txt"

echo.
echo Done! The browser has been opened to the frontend.
echo NOTE: Closing this window will also shut down the backend and frontend services.
pause
