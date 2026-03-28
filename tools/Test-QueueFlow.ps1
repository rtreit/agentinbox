<#
.SYNOPSIS
    End-to-end test for the Agent Inbox queue flow.
.DESCRIPTION
    Posts a synthetic GroupMe callback to the Azure Function URL,
    verifies it returns a "queued" response, then peeks the target
    queue to confirm the message was enqueued correctly.
.PARAMETER FunctionUrl
    The URL of the Azure Function HTTP trigger (e.g. https://myapp.azurewebsites.net/api/groupme).
.PARAMETER Agent
    Target agent name (default: "hal").
.PARAMETER ConnectionString
    Azure Storage account connection string for peeking the queue.
.PARAMETER QueuePrefix
    Queue name prefix (default: "agentinbox-").
.PARAMETER TimeoutSeconds
    How long to wait for the message to appear in the queue (default: 15).
.EXAMPLE
    .\Test-QueueFlow.ps1 -FunctionUrl "https://myapp.azurewebsites.net/api/groupme" -ConnectionString $connStr
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$FunctionUrl,

    [string]$Agent = "hal",

    [Parameter(Mandatory)]
    [string]$ConnectionString,

    [string]$QueuePrefix = "agentinbox-",

    [int]$TimeoutSeconds = 15
)

$ErrorActionPreference = "Stop"

$testId = [guid]::NewGuid().ToString("N").Substring(0, 8)
$testText = "@@$Agent test-message-$testId"
$queueName = "$QueuePrefix$Agent"

Write-Host "=== Agent Inbox End-to-End Test ===" -ForegroundColor Cyan
Write-Host "  Function URL : $FunctionUrl"
Write-Host "  Agent        : $Agent"
Write-Host "  Queue        : $queueName"
Write-Host "  Test ID      : $testId"
Write-Host ""

# Step 1: Post synthetic GroupMe callback
Write-Host "[1/3] Posting synthetic GroupMe callback..." -ForegroundColor Yellow

$payload = @{
    id          = "test-$testId"
    name        = "QueueFlowTest"
    text        = $testText
    sender_type = "user"
    group_id    = "test-group"
    user_id     = "test-user"
} | ConvertTo-Json -Compress

try {
    $response = Invoke-RestMethod -Uri $FunctionUrl -Method Post -Body $payload -ContentType "application/json" -ErrorAction Stop
} catch {
    Write-Error "POST to Function URL failed: $_"
    exit 1
}

$responseJson = $response | ConvertTo-Json -Compress -ErrorAction SilentlyContinue
Write-Host "  Response: $responseJson" -ForegroundColor Gray

if ($response.queued -ne $true) {
    Write-Error "Expected 'queued: true' in response but got: $responseJson"
    exit 1
}

Write-Host "  Function returned queued=true" -ForegroundColor Green

# Step 2: Peek the queue for the test message
Write-Host "`n[2/3] Peeking queue '$queueName' for test message..." -ForegroundColor Yellow

$found = $false
$elapsed = 0
$sleepInterval = 2

while ($elapsed -lt $TimeoutSeconds) {
    $peekResult = az storage message peek `
        --queue-name $queueName `
        --connection-string $ConnectionString `
        --num-messages 5 `
        --output json 2>$null | ConvertFrom-Json

    foreach ($msg in $peekResult) {
        $decoded = $null
        try {
            $decoded = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($msg.content))
        } catch {
            $decoded = $msg.content
        }

        if ($decoded -match $testId) {
            Write-Host "  Found test message in queue!" -ForegroundColor Green
            $found = $true
            $parsedMessage = $decoded | ConvertFrom-Json -ErrorAction SilentlyContinue
            break
        }
    }

    if ($found) { break }

    Start-Sleep -Seconds $sleepInterval
    $elapsed += $sleepInterval
    Write-Host "  Waiting... ($elapsed/$TimeoutSeconds seconds)" -ForegroundColor Gray
}

if (-not $found) {
    Write-Error "Test message not found in queue after $TimeoutSeconds seconds."
    exit 1
}

# Step 3: Validate the queued message
Write-Host "`n[3/3] Validating queued message..." -ForegroundColor Yellow

if ($parsedMessage) {
    Write-Host "  Parsed message:" -ForegroundColor Gray
    $parsedMessage | ConvertTo-Json -Depth 5 | Write-Host

    if ($parsedMessage.text -and $parsedMessage.text -match $testId) {
        Write-Host "  Message text matches test ID." -ForegroundColor Green
    } else {
        Write-Warning "Message text does not contain test ID '$testId'."
    }
} else {
    Write-Host "  Raw content: $decoded" -ForegroundColor Gray
}

Write-Host "`n=== Test PASSED ===" -ForegroundColor Green
