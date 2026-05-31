param(
  [string]$ServiceName = "traceguard",
  [string]$ImageTag = "",
  [int]$LocalPort = 18080,
  [switch]$SkipDockerBuild
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $repo

if (-not $ImageTag) {
  $ImageTag = "$ServiceName-local-verify"
}

function Assert-LastCommand($Action) {
  if ($LASTEXITCODE -ne 0) {
    throw "$Action failed with exit code $LASTEXITCODE"
  }
}

function Wait-ForHttp($Uri, $Seconds = 90) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  do {
    try {
      return Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5
    }
    catch {
      Start-Sleep -Seconds 2
    }
  } while ((Get-Date) -lt $deadline)

  throw "Timed out waiting for $Uri"
}

function Assert-Content($Label, $Content, $Pattern) {
  if ($Content -notmatch $Pattern) {
    throw "$Label did not contain expected marker: $Pattern"
  }
}

Write-Host "TraceGuard local verification gate"
if (-not $SkipDockerBuild) {
  Write-Host "1. Building local Docker image: $ImageTag"
  docker build -t $ImageTag .
  Assert-LastCommand "docker build"
}
else {
  Write-Host "1. Skipping Docker build and using existing image: $ImageTag"
}

Write-Host "2. Running unit/static tests inside the local image..."
docker run --rm $ImageTag python -m unittest discover -s tests -p "test_*.py"
Assert-LastCommand "container unit tests"

$containerName = "$ServiceName-local-verify-$PID-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
$baseUrl = "http://127.0.0.1:$LocalPort"

try {
  Write-Host "3. Starting local container on $baseUrl"
  docker run --rm -d `
    --name $containerName `
    -p "127.0.0.1:$LocalPort`:8080" `
    $ImageTag | Out-Null
  Assert-LastCommand "docker run"

  Write-Host "4. Checking local container health and UI markers..."
  $health = Wait-ForHttp "$baseUrl/health" 90
  if ($health.Content.Trim() -ne "ok") {
    throw "Local health check returned unexpected body: $($health.Content)"
  }

  $html = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/" -TimeoutSec 10
  Assert-Content "TraceGuard HTML" $html.Content "proofScoreboard"
  Assert-Content "TraceGuard HTML" $html.Content "Judge proof scoreboard"

  $appJs = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/app.js" -TimeoutSec 10
  Assert-Content "TraceGuard app.js" $appJs.Content "renderProofScoreboard"
  Assert-Content "TraceGuard app.js" $appJs.Content "gemini_validation_status"
  Assert-Content "TraceGuard app.js" $appJs.Content "unsupported_confirmed_claims"

  Write-Host "5. Running local agent smoke test..."
  $sample = Get-Content -Raw (Join-Path $repo "samples\gcp_incident_bundle.txt")
  $body = @{ evidence_text = $sample; mode = "improved" } | ConvertTo-Json -Depth 3
  $bodyBytes = [Text.Encoding]::UTF8.GetBytes($body)
  $result = Invoke-RestMethod -Uri "$baseUrl/api/analyze" `
    -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body $bodyBytes `
    -TimeoutSec 30

  if ($result.mode -ne "improved") {
    throw "Local agent smoke test returned unexpected mode: $($result.mode)"
  }
  if ([int]$result.metrics.unsupported_confirmed_claims -ne 0) {
    throw "Local agent smoke test found unsupported confirmed claims."
  }
  if ([int]$result.metrics.finding_count -lt 1) {
    throw "Local agent smoke test produced no findings."
  }
  if ([double]$result.metrics.eval_average -lt 0.8) {
    throw "Local agent smoke test eval average was below gate: $($result.metrics.eval_average)"
  }

  Write-Host "Local verification passed. Production deploy may continue."
}
finally {
  docker rm -f $containerName *> $null
}
