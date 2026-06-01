# TraceGuard Hosted Live Proof

This is the sanitized proof bundle from the deployed Cloud Run build. It is meant for judge review and does not include the private access key, Phoenix API key, cookies, or Secret Manager output.

## Deployment

- Date checked: June 1, 2026
- Google Cloud project: `project-e66ee676-19c8-4beb-bcb`
- Cloud Run service: `traceguard`
- Region: `us-central1`
- Public URL: `https://traceguard-cnhtsa5yrq-uc.a.run.app`
- Latest ready revision: exposed in `/proof` as `deployment.cloud_run_revision` and verified with Cloud Run service describe after deployment
- Traffic: `100%` to the latest ready revision

## Public Liveness

```json
{
  "health_status": 200,
  "root_head_status": 200,
  "proof_status": 200,
  "proof_head_status": 200,
  "proof_project": "TraceGuard",
  "proof_secrets_exposed": false,
  "proof_gemini_model": "gemini-3-flash-preview",
  "proof_latest_run_available": true,
  "auth_enabled": true,
  "authenticated": false
}
```

The hosted app is reachable, and `/proof` exposes only non-secret judge receipts. Protected demo routes stay locked until the Devpost judge key is accepted. That keeps the public URL testable without leaving the sample evidence and runtime details open to the internet.

## Authenticated Sample Run

This run used the private TraceGuard access key from Secret Manager. The key was not printed. The live `/proof` endpoint now includes this same shape under `latest_run`, with the current Cloud Run revision and pushed source commit filled in at runtime.

```json
{
  "evidence_items": 10,
  "findings": 11,
  "critical_or_high": 8,
  "eval_average": 0.94,
  "gemini_provider": "Google Cloud Gemini on Vertex AI",
  "gemini_model": "gemini-3-flash-preview",
  "gemini_ok": true,
  "gemini_validation_status": "pass",
  "gemini_accepted_claims": 10,
  "gemini_rejected_claims": 0,
  "arize_tracing_ready": true,
  "arize_phoenix_enabled": true,
  "arize_project": "traceguard-hackathon",
  "phoenix_mcp_status": "ok",
  "phoenix_mcp_tool_count": 27,
  "phoenix_mcp_queried_tools": ["list-traces"],
  "phoenix_mcp_queried_tool_count": 1,
  "phoenix_mcp_query_error": ""
}
```

Interpretation:

- The Cloud Run build is serving the current revision.
- Gemini 3 Flash Preview is live through Vertex AI and the evidence-ID validator passed with zero rejected evidence references.
- Phoenix/OpenTelemetry tracing is live for the `traceguard-hackathon` project.
- Phoenix MCP initialized, discovered tools, and completed a read-only query path.
- The deterministic report remains the source of truth; unsupported Gemini narrative claims are rejected rather than promoted into confirmed findings.
