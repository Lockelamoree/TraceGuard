param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectId,

  [string]$Region = "us-central1",
  [string]$GoogleCloudLocation = "global",
  [string]$ServiceName = "traceguard",
  [string]$RuntimeServiceAccountName = "traceguard-runtime",
  [string]$PhoenixSecretName = "traceguard-phoenix-api-key",
  [string]$AuthSecretName = "traceguard-auth-token",
  [string]$PhoenixBaseUrl = "https://app.phoenix.arize.com",
  [string]$PhoenixCollectorEndpoint = "",
  [string]$PhoenixMcpCommand = "npx -y @arizeai/phoenix-mcp@4.0.13",
  [string]$GeminiModel = "gemini-2.5-flash",
  [int]$AuthSessionSeconds = 43200
)

$ErrorActionPreference = "Stop"

$runtimeServiceAccount = "$RuntimeServiceAccountName@$ProjectId.iam.gserviceaccount.com"
$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$configDir = Join-Path $repo ".gcloud"
New-Item -ItemType Directory -Force $configDir | Out-Null

function Invoke-Gcloud {
  if (Get-Command gcloud -ErrorAction SilentlyContinue) {
    & gcloud @args
    return
  }
  $workspace = ($repo -replace "\\", "/")
  $config = ($configDir -replace "\\", "/")
  docker run --rm `
    -i `
    -v "${workspace}:/workspace" `
    -v "${config}:/root/.config/gcloud" `
    -w /workspace `
    "gcr.io/google.com/cloudsdktool/google-cloud-cli:slim" `
    gcloud @args
}

Invoke-Gcloud config set project $ProjectId
Invoke-Gcloud services enable `
  run.googleapis.com `
  aiplatform.googleapis.com `
  secretmanager.googleapis.com `
  cloudbuild.googleapis.com `
  artifactregistry.googleapis.com

$existingServiceAccount = Invoke-Gcloud iam service-accounts list `
  --filter "email:$runtimeServiceAccount" `
  --format "value(email)"
if (-not $existingServiceAccount) {
  Invoke-Gcloud iam service-accounts create $RuntimeServiceAccountName `
    --display-name "TraceGuard Cloud Run runtime"
}

Invoke-Gcloud projects add-iam-policy-binding $ProjectId `
  --member "serviceAccount:$runtimeServiceAccount" `
  --role "roles/aiplatform.user" `
  --quiet

$existingSecret = Invoke-Gcloud secrets list `
  --filter "name:$PhoenixSecretName" `
  --format "value(name)"
if (-not $existingSecret) {
  Write-Host "Create the Phoenix API key secret before deploying:"
  Write-Host "  .\deploy\docker-gcloud.ps1 secrets create $PhoenixSecretName --data-file=-"
  throw "Missing Secret Manager secret: $PhoenixSecretName"
}

$existingAuthSecret = Invoke-Gcloud secrets list `
  --filter "name:$AuthSecretName" `
  --format "value(name)"
if (-not $existingAuthSecret) {
  Write-Host "Create the TraceGuard auth secret before deploying:"
  Write-Host "  .\deploy\set-auth-secret.ps1 -ProjectId $ProjectId -Generate"
  throw "Missing Secret Manager secret: $AuthSecretName"
}

Invoke-Gcloud secrets add-iam-policy-binding $PhoenixSecretName `
  --member "serviceAccount:$runtimeServiceAccount" `
  --role "roles/secretmanager.secretAccessor" `
  --quiet

Invoke-Gcloud secrets add-iam-policy-binding $AuthSecretName `
  --member "serviceAccount:$runtimeServiceAccount" `
  --role "roles/secretmanager.secretAccessor" `
  --quiet

$envVars = @(
  "GOOGLE_CLOUD_PROJECT=$ProjectId",
  "GOOGLE_CLOUD_LOCATION=$GoogleCloudLocation",
  "GOOGLE_GENAI_USE_VERTEXAI=True",
  "ENABLE_GEMINI_SYNTHESIS=true",
  "GEMINI_MODEL=$GeminiModel",
  "PHOENIX_PROJECT_NAME=traceguard-hackathon",
  "PHOENIX_BASE_URL=$PhoenixBaseUrl",
  "PHOENIX_MCP_SERVER=@arizeai/phoenix-mcp",
  "TRACEGUARD_AUTH_SESSION_SECONDS=$AuthSessionSeconds"
)

if ($PhoenixCollectorEndpoint) {
  $envVars += "PHOENIX_COLLECTOR_ENDPOINT=$PhoenixCollectorEndpoint"
}
if ($PhoenixMcpCommand) {
  $envVars += "PHOENIX_MCP_COMMAND=$PhoenixMcpCommand"
}

Invoke-Gcloud run deploy $ServiceName `
  --source . `
  --region $Region `
  --service-account $runtimeServiceAccount `
  --allow-unauthenticated `
  --set-env-vars ($envVars -join ",") `
  --set-secrets "PHOENIX_API_KEY=$PhoenixSecretName`:latest,TRACEGUARD_AUTH_TOKEN=$AuthSecretName`:latest" `
  --quiet
