param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectId,

  [string]$PhoenixSecretName = "traceguard-phoenix-api-key"
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

Set-Location $repo
& $dockerGcloud config set project $ProjectId

$existingSecret = & $dockerGcloud secrets list `
  --project $ProjectId `
  --filter "name:$PhoenixSecretName" `
  --format "value(name)"
$secretExists = [bool]$existingSecret

Write-Host "Paste the Phoenix API key. It will be sent to Google Secret Manager and not written to disk."
$securePhoenixKey = Read-Host "Phoenix API key" -AsSecureString
$plainPhoenixKey = Convert-SecureStringToPlainText $securePhoenixKey
try {
  if ($secretExists) {
    $plainPhoenixKey | & $dockerGcloud secrets versions add $PhoenixSecretName --project $ProjectId --data-file=-
  }
  else {
    $plainPhoenixKey | & $dockerGcloud secrets create $PhoenixSecretName --project $ProjectId --data-file=-
  }
}
finally {
  $plainPhoenixKey = $null
}
