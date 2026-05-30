[CmdletBinding(PositionalBinding = $false)]
param(
  [Parameter(ValueFromPipeline = $true)]
  [string]$PipelineInput,

  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$GcloudArgs
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$configDir = Join-Path $repo ".gcloud"
New-Item -ItemType Directory -Force $configDir | Out-Null

$workspace = ($repo -replace "\\", "/")
$config = ($configDir -replace "\\", "/")
$image = "gcr.io/google.com/cloudsdktool/google-cloud-cli:slim"
$pipelineLines = New-Object System.Collections.Generic.List[string]

if ($null -ne $PipelineInput) {
  $pipelineLines.Add($PipelineInput)
}

if ($pipelineLines.Count -gt 0) {
  ($pipelineLines -join [Environment]::NewLine) | docker run --rm `
    -i `
    -v "${workspace}:/workspace" `
    -v "${config}:/root/.config/gcloud" `
    -w /workspace `
    $image `
    gcloud @GcloudArgs
}
else {
  docker run --rm `
    -i `
    -v "${workspace}:/workspace" `
    -v "${config}:/root/.config/gcloud" `
    -w /workspace `
    $image `
    gcloud @GcloudArgs
}

exit $LASTEXITCODE
