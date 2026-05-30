param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectId,

  [string]$AuthSecretName = "traceguard-auth-token",

  [switch]$Generate
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$dockerGcloud = Join-Path $repo "deploy\docker-gcloud.ps1"

function Convert-SecureStringToPlainText($SecureString) {
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureString)
  try {
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  }
  finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
}

function New-TraceGuardAccessKey {
  $bytes = New-Object byte[] 32
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  }
  finally {
    $rng.Dispose()
  }
  return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

Set-Location $repo
& $dockerGcloud config set project $ProjectId

$existingSecret = & $dockerGcloud secrets list `
  --project $ProjectId `
  --filter "name:$AuthSecretName" `
  --format "value(name)"
$secretExists = [bool]$existingSecret

if ($Generate) {
  $plainAuthToken = New-TraceGuardAccessKey
  Write-Host "Generated TraceGuard access key:"
  Write-Host $plainAuthToken
}
else {
  Write-Host "Paste the TraceGuard access key. It will be sent to Google Secret Manager and not written to disk."
  $secureAuthToken = Read-Host "TraceGuard access key" -AsSecureString
  $plainAuthToken = Convert-SecureStringToPlainText $secureAuthToken
}

try {
  if ($secretExists) {
    $plainAuthToken | & $dockerGcloud secrets versions add $AuthSecretName --project $ProjectId --data-file=-
  }
  else {
    $plainAuthToken | & $dockerGcloud secrets create $AuthSecretName --project $ProjectId --data-file=-
  }
}
finally {
  $plainAuthToken = $null
}
