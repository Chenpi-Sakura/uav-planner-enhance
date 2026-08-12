# Watches the start.bat console window that launched this watcher (its parent
# process). When that window is closed, terminates the backend and frontend
# process trees recorded by start.bat.

param(
    [Parameter(Mandatory = $true)]
    [string]$PidFile
)

$parentId = (Get-CimInstance Win32_Process -Filter "ProcessId = $PID" -ErrorAction SilentlyContinue).ParentProcessId

while ($true) {
    Start-Sleep -Seconds 2
    if (-not (Get-CimInstance Win32_Process -Filter "ProcessId = $parentId" -ErrorAction SilentlyContinue)) {
        # Terminate the process trees recorded by start.bat.
        if (Test-Path $PidFile) {
            foreach ($procId in (Get-Content $PidFile -ErrorAction SilentlyContinue)) {
                if ($procId -match '^\d+$') {
                    & taskkill /PID $procId /T /F 2>$null | Out-Null
                }
            }
        }
        # Fallback: kill any escaped uvicorn processes (including --reload workers).
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*uav-planner*' -and ($_.CommandLine -match 'uvicorn main:app' -or $_.CommandLine -match 'spawn_main') } |
            ForEach-Object { & taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }
        # Fallback: kill any escaped frontend (vite/npm) processes of this repo.
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like '*uav-planner-enhance*frontend*' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        break
    }
}