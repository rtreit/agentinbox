<#
.SYNOPSIS
    Uploads an image to GroupMe's image service and returns the CDN URL.
.DESCRIPTION
    Sends raw image bytes to https://image.groupme.com/pictures using a GroupMe
    user access token. The returned URL can be used as a bot avatar_url or in
    image attachments.
.PARAMETER Path
    Path to the local image file to upload.
.PARAMETER AccessToken
    GroupMe user access token. If omitted, uses GROUPME_ACCESS_TOKEN from the
    process environment or a nearby .env file.
.PARAMETER ContentType
    MIME type to send. If omitted, inferred from the file extension and falls
    back to image/jpeg.
.PARAMETER Quiet
    Emit only the uploaded CDN URL.
.EXAMPLE
    .\scripts\Upload-GroupMeImage.ps1 -Path .\avatar.png
.EXAMPLE
    .\scripts\Upload-GroupMeImage.ps1 `
      -Path .\avatar.jpg `
      -AccessToken $env:GROUPME_ACCESS_TOKEN `
      -Quiet
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Path,

    [string]$AccessToken,

    [string]$ContentType,

    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory)]
        [string]$Key,

        [Parameter(Mandatory)]
        [string]$StartDirectory
    )

    $current = (Get-Item -LiteralPath $StartDirectory -ErrorAction Stop).FullName

    for ($i = 0; $i -lt 5; $i++) {
        $envPath = Join-Path $current ".env"
        if (Test-Path -LiteralPath $envPath) {
            foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
                $trimmed = $line.Trim()
                if (-not $trimmed -or $trimmed.StartsWith("#")) {
                    continue
                }

                if ($trimmed -notmatch '^(?<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?<value>.*)$') {
                    continue
                }

                if ($Matches.name -ine $Key) {
                    continue
                }

                $value = $Matches.value.Trim()
                if (
                    ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                    ($value.StartsWith("'") -and $value.EndsWith("'"))
                ) {
                    $value = $value.Substring(1, $value.Length - 2)
                }

                return $value
            }
        }

        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) {
            break
        }

        $current = $parent
    }

    return $null
}

function Resolve-AccessToken {
    param([string]$ExplicitAccessToken)

    if ($ExplicitAccessToken) {
        return $ExplicitAccessToken
    }

    if ($env:GROUPME_ACCESS_TOKEN) {
        return $env:GROUPME_ACCESS_TOKEN
    }

    $searchRoots = @(
        (Get-Location).Path,
        (Split-Path -Parent $PSScriptRoot)
    ) | Select-Object -Unique

    foreach ($root in $searchRoots) {
        $token = Get-DotEnvValue -Key "GROUPME_ACCESS_TOKEN" -StartDirectory $root
        if ($token) {
            return $token
        }
    }

    return $null
}

function Resolve-UploadContentType {
    param(
        [Parameter(Mandatory)]
        [string]$FilePath,

        [string]$ExplicitContentType
    )

    if ($ExplicitContentType) {
        return $ExplicitContentType
    }

    switch ([System.IO.Path]::GetExtension($FilePath).ToLowerInvariant()) {
        ".jpg" { return "image/jpeg" }
        ".jpeg" { return "image/jpeg" }
        ".png" { return "image/png" }
        ".gif" { return "image/gif" }
        ".webp" { return "image/webp" }
        default { return "image/jpeg" }
    }
}

if (-not $AccessToken) {
    $AccessToken = Resolve-AccessToken -ExplicitAccessToken $AccessToken
}

if (-not $AccessToken) {
    throw "Provide -AccessToken, set GROUPME_ACCESS_TOKEN in your environment, or add it to a nearby .env file."
}

$resolvedPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
$file = Get-Item -LiteralPath $resolvedPath -ErrorAction Stop
if ($file.PSIsContainer) {
    throw "Path must refer to a file, not a directory: $resolvedPath"
}

$resolvedContentType = Resolve-UploadContentType -FilePath $file.FullName -ExplicitContentType $ContentType
$headers = @{
    "X-Access-Token" = $AccessToken
}

Write-Verbose "Uploading '$($file.FullName)' as '$resolvedContentType'"

try {
    $response = Invoke-RestMethod `
        -Uri "https://image.groupme.com/pictures" `
        -Method Post `
        -Headers $headers `
        -ContentType $resolvedContentType `
        -InFile $file.FullName
} catch {
    $details = $_.ErrorDetails.Message
    if ($details) {
        throw "GroupMe image upload failed: $details"
    }

    throw "GroupMe image upload failed: $($_.Exception.Message)"
}

$url = $response.payload.url
if (-not $url) {
    throw "Upload completed but no payload.url was returned."
}

if ($Quiet) {
    Write-Output $url
    return
}

[PSCustomObject]@{
    File        = $file.FullName
    ContentType = $resolvedContentType
    Url         = $url
    AvatarUrl   = "$url.avatar"
    PictureUrl  = $response.payload.picture_url
}
