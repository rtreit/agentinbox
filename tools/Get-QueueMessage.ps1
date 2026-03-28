<#
.SYNOPSIS
    Peek or read messages from an Azure Storage Queue.
.DESCRIPTION
    Inspects messages in an Agent Inbox queue. Decodes base64-encoded
    JSON payloads and pretty-prints them. Can optionally delete messages
    after reading.
.PARAMETER QueueName
    Name of the queue to inspect (default: "agentinbox-hal").
.PARAMETER ConnectionString
    Azure Storage account connection string.
.PARAMETER Peek
    Peek at messages without dequeuing them (default behavior).
.PARAMETER Delete
    Dequeue and delete messages after reading.
.PARAMETER Count
    Number of messages to retrieve (default: 1, max: 32).
.EXAMPLE
    .\Get-QueueMessage.ps1 -ConnectionString $connStr
.EXAMPLE
    .\Get-QueueMessage.ps1 -ConnectionString $connStr -QueueName agentinbox-stressbot -Delete -Count 5
#>

[CmdletBinding(DefaultParameterSetName = "Peek")]
param(
    [string]$QueueName = "agentinbox-hal",

    [Parameter(Mandatory)]
    [string]$ConnectionString,

    [Parameter(ParameterSetName = "Peek")]
    [switch]$Peek,

    [Parameter(ParameterSetName = "Delete")]
    [switch]$Delete,

    [ValidateRange(1, 32)]
    [int]$Count = 1
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI (az) not found. Install from https://aka.ms/installazurecli"
    exit 1
}

function Decode-MessageContent {
    param([string]$Content)

    # Try base64 decoding first
    try {
        $bytes = [System.Convert]::FromBase64String($Content)
        $decoded = [System.Text.Encoding]::UTF8.GetString($bytes)
    } catch {
        $decoded = $Content
    }

    # Try parsing as JSON
    try {
        $json = $decoded | ConvertFrom-Json
        return $json
    } catch {
        return $decoded
    }
}

Write-Host "Queue: $QueueName" -ForegroundColor Cyan

if ($Delete) {
    Write-Host "Mode: Dequeue + Delete ($Count message(s))" -ForegroundColor Yellow

    $messages = az storage message get `
        --queue-name $QueueName `
        --connection-string $ConnectionString `
        --num-messages $Count `
        --visibility-timeout 30 `
        --output json 2>$null | ConvertFrom-Json

    if (-not $messages -or $messages.Count -eq 0) {
        Write-Host "No messages in queue." -ForegroundColor Gray
        exit 0
    }

    foreach ($msg in $messages) {
        Write-Host "`n--- Message ---" -ForegroundColor Cyan
        Write-Host "  ID      : $($msg.id)"
        Write-Host "  Inserted: $($msg.insertionTime)"

        $parsed = Decode-MessageContent -Content $msg.content
        Write-Host "  Content :" -ForegroundColor Yellow
        if ($parsed -is [PSCustomObject] -or $parsed -is [hashtable]) {
            $parsed | ConvertTo-Json -Depth 5 | Write-Host
        } else {
            Write-Host "  $parsed"
        }

        # Delete the message
        az storage message delete `
            --queue-name $QueueName `
            --connection-string $ConnectionString `
            --id $msg.id `
            --pop-receipt $msg.popReceipt `
            --output none 2>$null

        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [DELETED]" -ForegroundColor Red
        } else {
            Write-Warning "  Failed to delete message $($msg.id)"
        }
    }
} else {
    # Peek mode (default)
    Write-Host "Mode: Peek ($Count message(s))" -ForegroundColor Yellow

    $messages = az storage message peek `
        --queue-name $QueueName `
        --connection-string $ConnectionString `
        --num-messages $Count `
        --output json 2>$null | ConvertFrom-Json

    if (-not $messages -or $messages.Count -eq 0) {
        Write-Host "No messages in queue." -ForegroundColor Gray
        exit 0
    }

    foreach ($msg in $messages) {
        Write-Host "`n--- Message ---" -ForegroundColor Cyan
        Write-Host "  ID      : $($msg.id)"
        Write-Host "  Inserted: $($msg.insertionTime)"

        $parsed = Decode-MessageContent -Content $msg.content
        Write-Host "  Content :" -ForegroundColor Yellow
        if ($parsed -is [PSCustomObject] -or $parsed -is [hashtable]) {
            $parsed | ConvertTo-Json -Depth 5 | Write-Host
        } else {
            Write-Host "  $parsed"
        }
    }
}

Write-Host "`nDone." -ForegroundColor Cyan
