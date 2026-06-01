# TraceGuard Hosted Live Proof

This is the sanitized proof bundle from the deployed Cloud Run build. It is meant for judge review and does not include Phoenix API keys, cookies, Secret Manager output, or other secret values.

## Deployment

- Date checked: June 1, 2026
- Google Cloud project: `project-e66ee676-19c8-4beb-bcb`
- Cloud Run service: `traceguard`
- Region: `us-central1`
- Public URL: `https://traceguard-cnhtsa5yrq-uc.a.run.app`
- Latest verified ready revision: `traceguard-00030-9jx`
- Latest verified source commit: `0a7e5a75256291162dcc5945427960a53c19ad54`
- Latest ready revision: exposed in `/proof` as `deployment.cloud_run_revision`
- Source commit: exposed in `/proof` as `deployment.source_commit`
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
  "auth_enabled": false,
  "authenticated": true
}
```

The hosted app is reachable, and `/proof` exposes only non-secret judge receipts. The judging deployment is public so reviewers can choose a bundled sample or load a redacted custom text sample and run the agent without an access key. Private deployments can still enable the signed-session gate with `TRACEGUARD_REQUIRE_AUTH=true`.

## Judge Receipt Behavior

The compact `Judge receipt` popover is not a static claim. It starts with pending labels, fetches `/proof` with `cache: no-store`, and fills the evidence, grounding, and Phoenix status tiles from the sanitized `latest_run` receipt. When a reviewer runs the agent, the same tiles update from that run's returned metrics. The hosted HTML was checked to confirm it no longer hardcodes `94% eval avg` or `0 unsupported claims`; those values appear only when the runtime receipt or current run supports them.

## Custom Sample Upload Safety

The deployed UI includes an `Upload sample` control for custom evidence bundles. It is intentionally browser-local and non-persistent: TraceGuard does not add a file-storage upload endpoint, and the selected file is copied into the evidence textarea only after local validation passes.

The guard accepts redacted text/log/JSON/JSONL/NDJSON/Terraform/YAML/Markdown samples under 1 MB. It blocks empty files, invalid UTF-8, binary/control-heavy content, unexpected file extensions or MIME types, and likely secrets such as private keys, Google API keys, AWS access keys, GitHub tokens, or long credential assignments. This is a safety check for demo evidence, not a replacement for proper secret scanning or DLP.

## Hosted Sample Run

This run used the hosted sample workflow. The live `/proof` endpoint includes this same shape under `latest_run`, with the current Cloud Run revision and pushed source commit filled in at runtime. If a new instance starts before a fresh run, the packaged fallback receipt is still stamped with the current runtime metadata before it is returned.

```json
{
  "source": "runtime_public_run",
  "cloud_run_revision": "<filled by /proof at runtime>",
  "source_commit": "<filled by /proof at runtime>",
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
