<#
.SYNOPSIS
    Session start hook - ensures the agent daemon is running.
.DESCRIPTION
    Runs automatically at the start of every Copilot CLI session.
    Checks if the daemon is already running, starts it if not,
    and prints recent daemon log entries for visibility.
#>

$repoRoot = git rev-parse --show-toplevel 2>$null
if (-not $repoRoot) { $repoRoot = $PSScriptRoot -replace '[\\/]\.github[\\/]hooks[\\/]scripts$', '' }

# Ensure daemon
$ensureScript = Join-Path $repoRoot "src\agentinbox\ensure_daemon.ps1"
if (Test-Path $ensureScript) {
    & $ensureScript 2>$null
}

# Show recent daemon activity
$logFile = Join-Path $repoRoot "logs\daemon.jsonl"
if (Test-Path $logFile) {
    $recent = Get-Content $logFile -Tail 5 -ErrorAction SilentlyContinue
    if ($recent) {
        Write-Host "Recent daemon activity:" -ForegroundColor DarkGray
        $recent | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    }
}
