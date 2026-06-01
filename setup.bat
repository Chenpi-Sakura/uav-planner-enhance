@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title UAV Planner Setup

echo ===================================================
echo     UAV Planner - Environment Setup
echo ===================================================
echo.

cd /d "%~dp0"

set "PY_EXE="

REM 1. Try system Python (py launcher or python on PATH)
for %%P in (py python) do (
    if not defined PY_EXE (
        %%P --version >nul 2>&1
        if not errorlevel 1 (
            set "PY_EXE=%%P"
        )
    )
)

REM 2. Try existing portable Python
if not defined PY_EXE (
    if exist "backend\.python\portable\python.exe" (
        set "PY_EXE=backend\.python\portable\python.exe"
    )
)

REM 3. No Python found: offer auto-download or manual install
if not defined PY_EXE (
    echo [!] No Python 3.10+ found on this system.
    echo.
    echo Choose an option:
    echo   [1] Auto-download Python 3.10 embeddable ^(~10 MB^)
    echo   [2] I'll install Python manually, then re-run this script
    echo.
    set /p CHOICE="Enter 1 or 2: "
    if "!CHOICE!"=="1" (
        echo.
        echo Downloading Python 3.10 embeddable...
        echo.

        if exist "%SystemRoot%\System32\curl.exe" (
            curl -L -o "%TEMP%\python-embed.zip" "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip"
        ) else (
            powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip' -OutFile '%TEMP%\python-embed.zip'}"
        )
        if errorlevel 1 (
            echo [X] Download failed. Check your network or choose option 2.
            pause
            exit /b 1
        )

        if not exist "backend\.python\portable" mkdir "backend\.python\portable"
        echo Extracting...
        powershell -Command "Expand-Archive -Path '%TEMP%\python-embed.zip' -DestinationPath 'backend\.python\portable' -Force"
        del "%TEMP%\python-embed.zip"

        REM Enable site-packages (embeddable Python disables it by default)
        for %%F in (backend\.python\portable\python*._pth) do (
            powershell -Command "(Get-Content '%%F') -replace '#import site', 'import site' | Set-Content '%%F'"
        )

        echo Installing pip...
        if exist "%SystemRoot%\System32\curl.exe" (
            curl -L -o "backend\.python\portable\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
        ) else (
            powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'backend\.python\portable\get-pip.py'}"
        )
        "backend\.python\portable\python.exe" "backend\.python\portable\get-pip.py"
        del "backend\.python\portable\get-pip.py"

        set "PY_EXE=backend\.python\portable\python.exe"
    ) else (
        echo.
        echo Please install Python 3.10+ from https://www.python.org/downloads/
        echo ^(Make sure to check "Add Python to PATH" during installation^)
        echo.
        pause
        exit /b 1
    )
)

echo [OK] Using Python: %PY_EXE%
%PY_EXE% --version
echo.

REM Call core installer
%PY_EXE% scripts\setup_core.py
if errorlevel 1 (
    echo.
    echo [X] Setup failed. See errors above.
    pause
    exit /b 1
)

echo.
echo Next step: run start.bat to launch the services.
echo.
pause
