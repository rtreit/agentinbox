<#
.SYNOPSIS
    Ensures the Agent Inbox daemon is running.
.DESCRIPTION
    Checks if agent_daemon is already running. If not, starts it
    in a visible conhost window. Designed to be called from session
    start hooks and interactive sessions.
#>

$repoRoot = $PSScriptRoot
while ($repoRoot -and -not (Test-Path (Join-Path $repoRoot "pyproject.toml"))) {
    $repoRoot = Split-Path $repoRoot -Parent
}

if (-not $repoRoot) {
    Write-Error "Could not find repo root (no pyproject.toml found)"
    exit 1
}

# Check if daemon is already running
$daemonProcs = Get-Process -Name python* -ErrorAction SilentlyContinue |
    Where-Object {
        try {
            $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
            $cmdLine -match 'agentinbox' -and $cmdLine -match 'daemon'
        } catch { $false }
    }

if ($daemonProcs) {
    $pid = $daemonProcs[0].Id
    Write-Host "Agent daemon already running (PID $pid)"
    exit 0
}

# Find Python in venv or PATH
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $pythonPath = $venvPython
} else {
    $pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $pythonPath) {
        $pythonPath = (Get-Command python3 -ErrorAction SilentlyContinue).Source
    }
}

if (-not $pythonPath) {
    Write-Error "Python not found. Run 'uv sync' first."
    exit 1
}

Write-Host "Starting agent daemon..."
Start-Process conhost.exe -ArgumentList $pythonPath, "-m", "agentinbox", "daemon" -WorkingDirectory $repoRoot
Write-Host "Agent daemon started in new window"
