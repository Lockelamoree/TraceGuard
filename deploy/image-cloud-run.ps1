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
  [string]$PhoenixMcpCommand = "phoenix-mcp",
  [int]$PhoenixMcpTimeoutSeconds = 12,
  [string]$GeminiModel = "gemini-3-flash-preview",
  [string]$Repository = "cloud-run-source-deploy",
  [int]$MaxInstances = 2,
  [int]$AuthSessionSeconds = 43200,
  [switch]$RequireAuth,
  [int]$LocalVerifyPort = 18080,
  [switch]$SkipLocalVerify
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$dockerGcloud = Join-Path $repo "deploy\docker-gcloud.ps1"
$localVerify = Join-Path $repo "deploy\local-verify.ps1"
$runtimeServiceAccount = "$RuntimeServiceAccountName@$ProjectId.iam.gserviceaccount.com"
$image = "$Region-docker.pkg.dev/$ProjectId/$Repository/$ServiceName`:latest"

Set-Location $repo

try {
  $SourceCommit = (& git rev-parse HEAD).Trim()
}
catch {
  $SourceCommit = ""
}

function Assert-LastCommand($Action) {
  if ($LASTEXITCODE -ne 0) {
    throw "$Action failed with exit code $LASTEXITCODE"
  }
}

function Get-CloudRunEnvVar($Name) {
  try {
    $value = & $dockerGcloud run services describe $ServiceName `
      --project $ProjectId `
      --region $Region `
      --format "value(spec.template.spec.containers[0].env[?name='$Name'].value)" 2>$null
    if ($LASTEXITCODE -ne 0) {
      return ""
    }
    return ($value | Select-Object -First 1)
  }
  catch {
    return ""
  }
}

function Resolve-PhoenixCollectorEndpoint($Candidate) {
  $endpoint = $Candidate.Trim()
  if (-not $endpoint) {
    $endpoint = (Get-CloudRunEnvVar "PHOENIX_COLLECTOR_ENDPOINT").Trim()
    if ($endpoint) {
      Write-Host "Reusing existing PHOENIX_COLLECTOR_ENDPOINT from Cloud Run."
    }
  }
  if (-not $endpoint) {
    throw "Phoenix collector endpoint is required for production deploy. Pass -PhoenixCollectorEndpoint 'https://app.phoenix.arize.com/s/traceroute'."
  }
  if ($endpoint.TrimEnd("/") -eq "https://app.phoenix.arize.com") {
    throw "Phoenix collector endpoint must be a space-specific URL, not https://app.phoenix.arize.com."
  }
  return $endpoint
}

if (-not $SkipLocalVerify) {
  & $localVerify -ServiceName $ServiceName -ImageTag "$ServiceName-local-prod" -LocalPort $LocalVerifyPort
}
else {
  Write-Host "Skipping local verification gate by request."
  docker build -t "$ServiceName-local-prod" .
  Assert-LastCommand "docker build"
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

if ($RequireAuth) {
  & $dockerGcloud secrets add-iam-policy-binding $AuthSecretName `
    --project $ProjectId `
    --member "serviceAccount:$runtimeServiceAccount" `
    --role "roles/secretmanager.secretAccessor" `
    --quiet
}

$PhoenixCollectorEndpoint = Resolve-PhoenixCollectorEndpoint $PhoenixCollectorEndpoint
$requireAuthValue = if ($RequireAuth) { "true" } else { "false" }

$token = & $dockerGcloud auth print-access-token
if (-not $token) {
  throw "No gcloud access token returned"
}
$token | docker login -u oauth2accesstoken --password-stdin "https://$Region-docker.pkg.dev"
Assert-LastCommand "docker login"

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
  "PHOENIX_MCP_TIMEOUT_SECONDS=$PhoenixMcpTimeoutSeconds",
  "TRACEGUARD_REQUIRE_AUTH=$requireAuthValue",
  "TRACEGUARD_AUTH_SESSION_SECONDS=$AuthSessionSeconds"
)

if ($SourceCommit) {
  $envVars += "TRACEGUARD_SOURCE_COMMIT=$SourceCommit"
}

if ($PhoenixMcpCommand) {
  $envVars += "PHOENIX_MCP_COMMAND=$PhoenixMcpCommand"
}

$secretBindings = @(
  "PHOENIX_API_KEY=$PhoenixSecretName`:latest"
)

if ($RequireAuth) {
  $secretBindings += "TRACEGUARD_AUTH_TOKEN=$AuthSecretName`:latest"
}

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
