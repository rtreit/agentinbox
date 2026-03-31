<#
.SYNOPSIS
    Updates AGENTINBOX_AGENT_PERSONAS on an Azure Function App.
.DESCRIPTION
    Reads per-agent persona definitions from inline JSON or a JSON file,
    normalizes them, and writes the compressed JSON to the
    AGENTINBOX_AGENT_PERSONAS app setting on the target Function App.

    Persona definitions are keyed by agent name. Each value may be either:
      - a string (treated as the instructions)
      - an object with id, version, and instructions fields

    Use -Preview to validate and print the normalized JSON without calling Azure.
.PARAMETER FunctionApp
    Azure Function App name.
.PARAMETER ResourceGroup
    Azure resource group for the Function App.
.PARAMETER PersonasJson
    Inline JSON string keyed by agent name.
.PARAMETER PersonasFile
    Path to a JSON file keyed by agent name.
.PARAMETER Preview
    Validate and print the normalized JSON without updating Azure.
.EXAMPLE
    .\Set-FunctionAppPersonas.ps1 `
      -FunctionApp my-agentinbox `
      -ResourceGroup agentinbox `
      -PersonasJson '{"hal":{"instructions":"You are HAL. Be calm and concise."}}'
.EXAMPLE
    .\Set-FunctionAppPersonas.ps1 `
      -FunctionApp my-agentinbox `
      -ResourceGroup agentinbox `
      -PersonasFile .\personas.json
.EXAMPLE
    .\Set-FunctionAppPersonas.ps1 -PersonasFile .\personas.json -Preview
#>

[CmdletBinding(DefaultParameterSetName = "Inline")]
param(
    [Parameter(Mandatory, ParameterSetName = "Inline")]
    [string]$PersonasJson,

    [Parameter(Mandatory, ParameterSetName = "File")]
    [string]$PersonasFile,

    [Parameter(ParameterSetName = "Inline")]
    [Parameter(ParameterSetName = "File")]
    [string]$FunctionApp,

    [Parameter(ParameterSetName = "Inline")]
    [Parameter(ParameterSetName = "File")]
    [string]$ResourceGroup,

    [switch]$Preview
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-MemberEntries {
    param([Parameter(Mandatory)]$Object)

    if ($Object -is [System.Collections.IDictionary]) {
        return $Object.GetEnumerator() | ForEach-Object {
            [PSCustomObject]@{
                Name = [string]$_.Key
                Value = $_.Value
            }
        }
    }

    return $Object.PSObject.Properties |
        Where-Object { $_.MemberType -in @("NoteProperty", "Property") } |
        ForEach-Object {
            [PSCustomObject]@{
                Name = $_.Name
                Value = $_.Value
            }
        }
}

function Get-OptionalPropertyValue {
    param(
        [Parameter(Mandatory)]$Object,
        [Parameter(Mandatory)][string]$Name
    )

    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) {
            return $Object[$Name]
        }
        return $null
    }

    $prop = $Object.PSObject.Properties[$Name]
    if ($null -ne $prop) {
        return $prop.Value
    }

    return $null
}

function Get-SourceJson {
    if ($PSCmdlet.ParameterSetName -eq "File") {
        if (-not (Test-Path -LiteralPath $PersonasFile)) {
            throw "Personas file not found: $PersonasFile"
        }
        return Get-Content -LiteralPath $PersonasFile -Raw -Encoding UTF8
    }

    return $PersonasJson
}

function Normalize-Personas {
    param([Parameter(Mandatory)][string]$JsonText)

    try {
        $parsed = $JsonText | ConvertFrom-Json
    } catch {
        throw "Personas JSON is not valid: $($_.Exception.Message)"
    }

    if ($null -eq $parsed -or $parsed -is [System.Array]) {
        throw "Personas JSON must be an object keyed by agent name."
    }

    $normalized = [ordered]@{}
    foreach ($entry in Get-MemberEntries -Object $parsed) {
        $agentName = [string]$entry.Name
        $agentKey = $agentName.Trim().ToLowerInvariant()
        if (-not $agentKey) {
            continue
        }

        $personaValue = $entry.Value
        $instructions = ""
        $personaId = $agentKey
        $version = ""

        if ($personaValue -is [string]) {
            $instructions = $personaValue.Trim()
        } elseif ($null -ne $personaValue -and -not ($personaValue -is [System.Array])) {
            $instructionsValue = Get-OptionalPropertyValue -Object $personaValue -Name "instructions"
            if ($instructionsValue -is [string]) {
                $instructions = $instructionsValue.Trim()
            }

            $idValue = Get-OptionalPropertyValue -Object $personaValue -Name "id"
            if ($idValue -is [string] -and -not [string]::IsNullOrWhiteSpace($idValue)) {
                $personaId = $idValue.Trim()
            }

            $versionValue = Get-OptionalPropertyValue -Object $personaValue -Name "version"
            if ($null -ne $versionValue) {
                $version = ([string]$versionValue).Trim()
            }
        } else {
            throw "Persona '$agentKey' must be a string or object."
        }

        if ([string]::IsNullOrWhiteSpace($instructions)) {
            throw "Persona '$agentKey' is missing non-empty instructions."
        }

        $persona = [ordered]@{
            id = $personaId
        }
        if (-not [string]::IsNullOrWhiteSpace($version)) {
            $persona["version"] = $version
        }
        $persona["instructions"] = $instructions

        $normalized[$agentKey] = $persona
    }

    if ($normalized.Count -eq 0) {
        throw "No valid persona entries were found."
    }

    return $normalized
}

$sourceJson = Get-SourceJson
$personas = Normalize-Personas -JsonText $sourceJson
$compressed = $personas | ConvertTo-Json -Depth 10 -Compress

if ($Preview) {
    Write-Host "Normalized AGENTINBOX_AGENT_PERSONAS value:" -ForegroundColor Cyan
    Write-Output $compressed
    return
}

if ([string]::IsNullOrWhiteSpace($FunctionApp)) {
    throw "-FunctionApp is required unless -Preview is used."
}

if ([string]::IsNullOrWhiteSpace($ResourceGroup)) {
    throw "-ResourceGroup is required unless -Preview is used."
}

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI (az) not found. Install from https://aka.ms/installazurecli"
}

$setting = "AGENTINBOX_AGENT_PERSONAS=$compressed"

az functionapp config appsettings set `
    --name $FunctionApp `
    --resource-group $ResourceGroup `
    --settings $setting `
    --output none

if ($LASTEXITCODE -ne 0) {
    throw "Failed to update AGENTINBOX_AGENT_PERSONAS on Function App '$FunctionApp'."
}

Write-Host "Updated AGENTINBOX_AGENT_PERSONAS on Function App '$FunctionApp'." -ForegroundColor Green
Write-Host "  Resource group: $ResourceGroup" -ForegroundColor DarkGray
Write-Host "  Persona count:   $($personas.Count)" -ForegroundColor DarkGray
