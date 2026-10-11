# Stops whatever the PREVIOUS start.bat run left alive, so a fresh run starts from
# a clean slate. Called by start.bat before it launches anything.
#
# Why this exists: scripts/start_watcher.ps1 watches its own parent start window
# and, when that window disappears, taskkills every PID in the SHARED file
# %TEMP%\uav_planner_pids.txt. start.bat overwrites that file with the NEW stack's
# PIDs, so a watcher from an older run that dies after the new run has started
# tears the new stack down. Observed 2026-10-11: backend, vite and frpc all
# vanished minutes after a clean start, while the OLD frpc survived (the watcher's
# fallback sweep does not match frpc) and kept the tunnel name, so the new frpc
# logged "proxy [uav-local] already exists" and the public site served 502 with a
# tunnel that looked healthy.
#
# Order matters: watchers first (otherwise one of them fires mid-cleanup and kills
# the stack we are about to launch), then the recorded trees, then a fallback sweep
# for escaped processes, then wait for the backend port to actually be free --
# uvicorn's bind failure used to be indistinguishable from "start.bat never
# started the backend".
#
# ASCII only: this runs in a console whose code page we do not control.

param(
    [Parameter(Mandatory = $true)][string]$PidFile,
    [int]$BackendPort = 9090,
    [int]$PortWaitSeconds = 15
)

$ErrorActionPreference = 'SilentlyContinue'
$repo = Split-Path -Parent $PSScriptRoot

# Our own PID plus every ancestor. The start window that invoked us is in here,
# and its command line contains this repo's path -- a pattern kill that matched it
# would take the whole startup down mid-run.
$self = @($PID)
$probe = Get-CimInstance Win32_Process -Filter "ProcessId = $PID"
while ($probe -and $probe.ParentProcessId) {
    $self += $probe.ParentProcessId
    $probe = Get-CimInstance Win32_Process -Filter "ProcessId = $($probe.ParentProcessId)"
}

$killed = 0

function Stop-Tree {
    param([uint32]$TargetId, [string]$Why)
    if ($self -contains $TargetId) { return }
    if (-not (Get-Process -Id $TargetId -ErrorAction SilentlyContinue)) { return }
    Write-Output ("  stop {0,6}  {1}" -f $TargetId, $Why)
    & taskkill /PID $TargetId /T /F 2>$null | Out-Null
    $script:killed++
}

# 1. Leftover watchers from earlier runs.
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match '^(powershell|pwsh)\.exe$' -and $_.CommandLine -match 'start_watcher\.ps1' } |
    ForEach-Object { Stop-Tree -TargetId $_.ProcessId -Why 'orphaned start_watcher' }

# 2. Whatever the previous run recorded.
if (Test-Path $PidFile) {
    foreach ($line in Get-Content $PidFile) {
        if ($line -match '^\s*(\d+)\s*$') {
            Stop-Tree -TargetId ([uint32]$Matches[1]) -Why 'recorded by the previous run'
        }
    }
    Remove-Item $PidFile -Force
}

# 3. Fallback for processes that escaped the PID file. Same signatures the watcher
#    uses, plus frpc -- which the watcher never matched, and which is exactly what
#    stayed behind and squatted on the tunnel name.
Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq 'python.exe' -and $_.CommandLine -like "*$repo*" -and
        ($_.CommandLine -match 'uvicorn main:app' -or $_.CommandLine -match 'spawn_main')
    } | ForEach-Object { Stop-Tree -TargetId $_.ProcessId -Why 'escaped uvicorn' }

Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -like "*$repo*frontend*" } |
    ForEach-Object { Stop-Tree -TargetId $_.ProcessId -Why 'escaped vite / npm' }

Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq 'frpc.exe' -and $_.ExecutablePath -like "$repo\frp\*" } |
    ForEach-Object { Stop-Tree -TargetId $_.ProcessId -Why 'stale frpc holding the tunnel' }

# 4. Wait for the backend port to be released before we report.
$heldBy = $null
$deadline = (Get-Date).AddSeconds($PortWaitSeconds)
while ((Get-Date) -lt $deadline) {
    $held = Get-NetTCPConnection -LocalPort $BackendPort -State Listen
    if (-not $held) { $heldBy = $null; break }
    $heldBy = $held[0].OwningProcess
    Start-Sleep -Milliseconds 500
}

if ($heldBy) {
    Write-Output "  [WARN] port $BackendPort is still held by PID $heldBy"
} elseif ($killed -gt 0) {
    Write-Output "  previous run stopped ($killed process tree(s)), port $BackendPort free"
}
