# Pre-tool policy hook
# Validates that file changes don't introduce secrets.

param(
    [string]$File = $env:COPILOT_FILE
)

function Test-ForSecrets {
    param([string]$FilePath)
    if (Test-Path $FilePath) {
        $content = Get-Content $FilePath -Raw -ErrorAction SilentlyContinue
        if ($content -match '(?i)(api_key|secret|password|token|connection_string)\s*[=:]\s*["\x27][^"\x27]{8,}["\x27]') {
            Write-Error "POLICY VIOLATION: Potential hardcoded secret detected in $FilePath"
            Write-Error "Use environment variables or .env file instead."
            exit 1
        }
    }
}

if ($File) {
    Test-ForSecrets -FilePath $File
}
