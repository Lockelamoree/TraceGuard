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
  [string]$PhoenixCollectorEndpoint = "https://app.phoenix.arize.com",
  [string]$PhoenixMcpCommand = "npx -y @arizeai/phoenix-mcp@4.0.13",
  [string]$GeminiModel = "gemini-2.5-flash",
  [string]$Repository = "cloud-run-source-deploy",
  [int]$MaxInstances = 2,
  [int]$AuthSessionSeconds = 43200
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$dockerGcloud = Join-Path $repo "deploy\docker-gcloud.ps1"
$runtimeServiceAccount = "$RuntimeServiceAccountName@$ProjectId.iam.gserviceaccount.com"
$image = "$Region-docker.pkg.dev/$ProjectId/$Repository/$ServiceName`:latest"

Set-Location $repo

function Assert-LastCommand($Action) {
  if ($LASTEXITCODE -ne 0) {
    throw "$Action failed with exit code $LASTEXITCODE"
  }
}

& $dockerGcloud config set project $ProjectId
& $dockerGcloud services enable `
  run.googleapis.com `
  aiplatform.googleapis.com `
  secretmanager.googleapis.com `
  artifactregistry.googleapis.com

$existingServiceAccount = & $dockerGcloud iam service-accounts list `
  --filter "email:$runtimeServiceAccount" `
  --format "value(email)"
if (-not $existingServiceAccount) {
  & $dockerGcloud iam service-accounts create $RuntimeServiceAccountName `
    --display-name "TraceGuard Cloud Run runtime"
}

& $dockerGcloud projects add-iam-policy-binding $ProjectId `
  --member "serviceAccount:$runtimeServiceAccount" `
  --role "roles/aiplatform.user" `
  --quiet

& $dockerGcloud secrets add-iam-policy-binding $PhoenixSecretName `
  --project $ProjectId `
  --member "serviceAccount:$runtimeServiceAccount" `
  --role "roles/secretmanager.secretAccessor" `
  --quiet

& $dockerGcloud secrets add-iam-policy-binding $AuthSecretName `
  --project $ProjectId `
  --member "serviceAccount:$runtimeServiceAccount" `
  --role "roles/secretmanager.secretAccessor" `
  --quiet

$token = & $dockerGcloud auth print-access-token
if (-not $token) {
  throw "No gcloud access token returned"
}
$token | docker login -u oauth2accesstoken --password-stdin "https://$Region-docker.pkg.dev"
Assert-LastCommand "docker login"

docker build -t "$ServiceName-local-prod" .
Assert-LastCommand "docker build"
docker tag "$ServiceName-local-prod`:latest" $image
Assert-LastCommand "docker tag"
docker push $image
Assert-LastCommand "docker push"

$envVars = @(
  "GOOGLE_CLOUD_PROJECT=$ProjectId",
  "GOOGLE_CLOUD_LOCATION=$GoogleCloudLocation",
  "GOOGLE_GENAI_USE_VERTEXAI=True",
  "ENABLE_GEMINI_SYNTHESIS=true",
  "GEMINI_MODEL=$GeminiModel",
  "PHOENIX_PROJECT_NAME=traceguard-hackathon",
  "PHOENIX_BASE_URL=$PhoenixBaseUrl",
  "PHOENIX_MCP_SERVER=@arizeai/phoenix-mcp",
  "PHOENIX_COLLECTOR_ENDPOINT=$PhoenixCollectorEndpoint",
  "TRACEGUARD_AUTH_SESSION_SECONDS=$AuthSessionSeconds"
)

if ($PhoenixMcpCommand) {
  $envVars += "PHOENIX_MCP_COMMAND=$PhoenixMcpCommand"
}

$secretBindings = @(
  "PHOENIX_API_KEY=$PhoenixSecretName`:latest",
  "TRACEGUARD_AUTH_TOKEN=$AuthSecretName`:latest"
)

& $dockerGcloud run deploy $ServiceName `
  --project $ProjectId `
  --region $Region `
  --image $image `
  --service-account $runtimeServiceAccount `
  --allow-unauthenticated `
  --max-instances $MaxInstances `
  --set-env-vars ($envVars -join ",") `
  --set-secrets ($secretBindings -join ",") `
  --quiet
Assert-LastCommand "gcloud run deploy"
