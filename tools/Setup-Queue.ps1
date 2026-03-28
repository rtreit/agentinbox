<#
.SYNOPSIS
    Provisions Azure Storage Queues for Agent Inbox agents.
.DESCRIPTION
    Creates one queue per agent using the Azure CLI. Queue names follow
    the pattern {QueuePrefix}{agent}, e.g. "agentinbox-hal".
.PARAMETER ConnectionString
    Azure Storage account connection string.
.PARAMETER Agents
    Comma-separated list of agent names (default: "hal").
.PARAMETER QueuePrefix
    Prefix for queue names (default: "agentinbox-").
.PARAMETER FunctionApp
    Optional Azure Function App name. When provided, sets the
    STORAGE_CONNECTION_STRING app setting on the Function App.
.PARAMETER ResourceGroup
    Resource group for the Function App (required with -FunctionApp).
.EXAMPLE
    .\Setup-Queue.ps1 -ConnectionString $connStr
.EXAMPLE
    .\Setup-Queue.ps1 -ConnectionString $connStr -Agents "hal,stressbot" -FunctionApp myapp -ResourceGroup myrg
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ConnectionString,

    [string]$Agents = "hal",

    [string]$QueuePrefix = "agentinbox-",

    [string]$FunctionApp,

    [string]$ResourceGroup
)

$ErrorActionPreference = "Stop"

# Validate Azure CLI is available
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI (az) not found. Install from https://aka.ms/installazurecli"
    exit 1
}

$agentList = $Agents -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ }

if ($agentList.Count -eq 0) {
    Write-Error "No agents specified."
    exit 1
}

Write-Host "Provisioning queues for $($agentList.Count) agent(s)..." -ForegroundColor Cyan

foreach ($agent in $agentList) {
    $queueName = "$QueuePrefix$agent"
    Write-Host "  Creating queue: $queueName" -ForegroundColor Yellow

    az storage queue create `
        --name $queueName `
        --connection-string $ConnectionString `
        --output none

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create queue '$queueName'"
        exit 1
    }

    Write-Host "  Queue '$queueName' created." -ForegroundColor Green
}

# Optionally configure Function App settings
if ($FunctionApp) {
    if (-not $ResourceGroup) {
        Write-Error "-ResourceGroup is required when -FunctionApp is specified."
        exit 1
    }

    Write-Host "`nConfiguring Function App '$FunctionApp'..." -ForegroundColor Cyan

    az functionapp config appsettings set `
        --name $FunctionApp `
        --resource-group $ResourceGroup `
        --settings "STORAGE_CONNECTION_STRING=$ConnectionString" `
        --output none

    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to set Function App settings."
        exit 1
    }

    Write-Host "  Function App settings updated." -ForegroundColor Green
}

Write-Host "`nDone. All queues provisioned." -ForegroundColor Cyan
