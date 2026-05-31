param(
  [string]$ProjectId = "",
  [string]$Region = "us-central1",
  [string]$GoogleCloudLocation = "global",
  [string]$ServiceName = "traceguard",
  [string]$PhoenixSecretName = "traceguard-phoenix-api-key",
  [string]$AuthSecretName = "traceguard-auth-token",
  [string]$PhoenixBaseUrl = "https://app.phoenix.arize.com",
  [string]$PhoenixCollectorEndpoint = "",
  [string]$PhoenixMcpCommand = "phoenix-mcp",
  [int]$PhoenixMcpTimeoutSeconds = 12,
  [string]$GeminiModel = "gemini-2.5-flash",
  [int]$AuthSessionSeconds = 43200,
  [int]$LocalVerifyPort = 18080,
  [switch]$SkipLocalVerify
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$dockerGcloud = Join-Path $repo "deploy\docker-gcloud.ps1"
$cloudRunDeploy = Join-Path $repo "deploy\cloud-run.ps1"

function Invoke-Gcloud {
  & $dockerGcloud @args
}

function Read-Required($Prompt, $Default = "") {
  $suffix = if ($Default) { " [$Default]" } else { "" }
  $value = Read-Host "$Prompt$suffix"
  if (-not $value -and $Default) {
    return $Default
  }
  if (-not $value) {
    throw "$Prompt is required"
  }
  return $value
}

function Read-YesNo($Prompt, [bool]$Default = $false) {
  $defaultLabel = if ($Default) { "Y/n" } else { "y/N" }
  $value = Read-Host "$Prompt [$defaultLabel]"
  if (-not $value) {
    return $Default
  }
  return $value.Trim().ToLowerInvariant().StartsWith("y")
}

function Convert-SecureStringToPlainText($SecureString) {
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureString)
  try {
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  }
  finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
}

Set-Location $repo

Write-Host "TraceGuard production wizard"
Write-Host "This stores gcloud auth under .gcloud/ and runtime secrets in Google Secret Manager."
Write-Host ""

$authJson = Invoke-Gcloud auth list --filter "status:ACTIVE" --format "value(account)"
if (-not $authJson) {
  Write-Host "No active gcloud account found. Complete the browser auth flow when prompted."
  Invoke-Gcloud auth login --no-launch-browser
}

$ProjectId = Read-Required "Google Cloud project ID" $ProjectId
$Region = Read-Required "Cloud Run region" $Region
$GoogleCloudLocation = Read-Required "Vertex AI Gemini location" $GoogleCloudLocation
$PhoenixBaseUrl = Read-Required "Phoenix API base URL" $PhoenixBaseUrl
$PhoenixCollectorEndpoint = Read-Required "Phoenix collector endpoint or Phoenix Cloud space URL" $PhoenixCollectorEndpoint
$GeminiModel = Read-Required "Gemini model" $GeminiModel

Invoke-Gcloud config set project $ProjectId

$existingPhoenixSecret = Invoke-Gcloud secrets list `
  --filter "name:$PhoenixSecretName" `
  --format "value(name)"
$secretExists = [bool]$existingPhoenixSecret

if ($secretExists -and -not (Read-YesNo "Existing Phoenix API key secret found. Add a new secret version?" $false)) {
  Write-Host "Keeping existing Phoenix API key secret."
}
else {
  Write-Host ""
  Write-Host "Paste the Phoenix API key when prompted. It will not be printed."
  $securePhoenixKey = Read-Host "Phoenix API key" -AsSecureString
  $plainPhoenixKey = Convert-SecureStringToPlainText $securePhoenixKey
  try {
    if (-not $plainPhoenixKey) {
      throw "Phoenix API key cannot be empty"
    }
    if ($secretExists) {
      $plainPhoenixKey | & $dockerGcloud secrets versions add $PhoenixSecretName --data-file=-
    }
    else {
      $plainPhoenixKey | & $dockerGcloud secrets create $PhoenixSecretName --data-file=-
    }
  }
  finally {
    $plainPhoenixKey = $null
  }
}

$existingAuthSecret = Invoke-Gcloud secrets list `
  --filter "name:$AuthSecretName" `
  --format "value(name)"
$authSecretExists = [bool]$existingAuthSecret

if ($authSecretExists -and -not (Read-YesNo "Existing TraceGuard access key secret found. Add a new secret version?" $false)) {
  Write-Host "Keeping existing TraceGuard access key secret."
}
else {
  Write-Host ""
  Write-Host "Paste a long TraceGuard access key when prompted. Judges use this to unlock the hosted demo."
  $secureAuthToken = Read-Host "TraceGuard access key" -AsSecureString
  $plainAuthToken = Convert-SecureStringToPlainText $secureAuthToken
  try {
    if (-not $plainAuthToken) {
      throw "TraceGuard access key cannot be empty"
    }
    if ($authSecretExists) {
      $plainAuthToken | & $dockerGcloud secrets versions add $AuthSecretName --data-file=-
    }
    else {
      $plainAuthToken | & $dockerGcloud secrets create $AuthSecretName --data-file=-
    }
  }
  finally {
    $plainAuthToken = $null
  }
}

& $cloudRunDeploy `
  -ProjectId $ProjectId `
  -Region $Region `
  -GoogleCloudLocation $GoogleCloudLocation `
  -ServiceName $ServiceName `
  -PhoenixSecretName $PhoenixSecretName `
  -AuthSecretName $AuthSecretName `
  -PhoenixBaseUrl $PhoenixBaseUrl `
  -PhoenixCollectorEndpoint $PhoenixCollectorEndpoint `
  -PhoenixMcpCommand $PhoenixMcpCommand `
  -PhoenixMcpTimeoutSeconds $PhoenixMcpTimeoutSeconds `
  -GeminiModel $GeminiModel `
  -AuthSessionSeconds $AuthSessionSeconds `
  -LocalVerifyPort $LocalVerifyPort `
  -SkipLocalVerify:$SkipLocalVerify
