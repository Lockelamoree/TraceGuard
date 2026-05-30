# TraceGuard Devpost Submission Draft

## Project Summary

I built TraceGuard for cloud security triage when the evidence is scattered across logs, IAM JSON, Terraform, alert text, and repository settings. The agent reads that bundle, finds the security-relevant facts, and produces a report with evidence IDs, confidence, impact, remediation, detection ideas, CWE references, and MITRE ATT&CK mappings.

My main constraint was evidence discipline: if TraceGuard cannot back a claim with parsed evidence, it should not dress it up as confirmed. Security teams already have enough confident nonsense to clean up.

TraceGuard is my Arize track submission. The hosted path uses Cloud Run, Gemini on Vertex AI for narrative synthesis, Phoenix/OpenTelemetry spans for run visibility, a pinned Phoenix MCP command for read-only tool discovery, and evals that catch unsupported or low-quality report output.

Live project URL: https://traceguard-cnhtsa5yrq-uc.a.run.app

Public repository URL: https://github.com/Lockelamoree/TraceGuard

Judge access: if the hosted app asks for an access key, use the temporary judge key provided in the Devpost submission field. The local demo needs no cloud credentials and still shows deterministic triage, baseline/improved comparison, evals, and report export.

## Judge Quickstart

1. Open the hosted URL or run locally.
2. Unlock with the Devpost judge key if prompted.
3. Click `Load sample`.
4. Click `Run agent`.
5. Confirm the baseline/improved delta:
   - Baseline: 9 findings.
   - Improved: 11 findings.
   - Public access moves to critical.
   - Disabled branch protection and secret scanning become explicit repo-control findings.
6. Open the final report preview and check that confirmed findings cite evidence IDs.

Local command:

```powershell
python -m traceguard.server --host 127.0.0.1 --port 8000
```

Windows launcher fallback:

```powershell
py -3.11 -m traceguard.server --host 127.0.0.1 --port 8000
```

## How The Workflow Fits Together

```mermaid
flowchart TD
    evidence["Evidence bundle"] --> parser["Parser"]
    parser --> agent["Deterministic triage agent"]
    agent --> evals["Quality evals"]
    evals --> report["Evidence-backed report"]
    agent -. "optional hosted synthesis" .-> gemini["Gemini on Vertex AI"]
    gemini -. "summarizes findings" .-> report
    agent -. "spans" .-> phoenix["Phoenix / OpenTelemetry"]
    evals -. "scores" .-> phoenix
    agent -. "read-only initialize, tools/list, list-projects, list-traces" .-> mcp["Phoenix MCP"]
    mcp -. "status and tool count" .-> status["Runtime badges / run steps"]
    phoenix -. "trace status" .-> status
```

More detail lives in `PROJECT_VISUALIZATION.md`.

## Claims Matrix

| Area | Confirmed local | Confirmed hosted after judge login | Optional/live-only |
| --- | --- | --- | --- |
| Deterministic triage | Yes | Yes | No |
| Baseline/improved delta | Yes | Yes | No |
| Gemini on Vertex AI | Disabled without env vars | Yes when runtime badge reports Gemini live | Requires Google Cloud project/config |
| Phoenix OTEL | Local replay without env vars | Yes when runtime badge reports Phoenix OTEL live | Requires Phoenix key/collector |
| Phoenix MCP | Local replay without env vars | Yes when runtime badge reports MCP live | Requires pinned `PHOENIX_MCP_COMMAND` |

## What It Does

- Parses mixed cloud incident evidence into structured artifacts.
- Checks for IAM over-privilege, public resource exposure, suspicious token/policy activity, broad network ingress, and disabled repo controls.
- Produces a report where every confirmed finding cites evidence IDs.
- Shows a baseline run and an improved run so the delta is visible.
- Runs evals for evidence grounding, confirmed-claim hygiene, detection usefulness, remediation usefulness, severity calibration, and duplicate pressure.
- Renders the final report in-app and keeps a clipboard export for handoff.

## Technologies Used

- Google Cloud deployment target: Cloud Run.
- Google Cloud AI target: Gemini on Vertex AI through the Google Gen AI SDK version line required by Google ADK.
- Agent surface: ADK-compatible `root_agent` in `traceguard/adk_agent.py`.
- Arize: Phoenix Cloud, Phoenix OTEL instrumentation, and a stdio Phoenix MCP client that performs read-only `initialize`, `tools/list`, `list-projects`, and `list-traces` when configured and supported by the server.
- Python standard library for the local backend.
- HTML, CSS, and JavaScript for the judge-facing web app.

## Arize Integration Story

I treated observability as part of the agent loop, not a screenshot at the end. Each triage run has traceable phases: parsing, finding derivation, validation, evals, Gemini synthesis, Phoenix MCP introspection, and report generation.

In production mode, Phoenix/OpenTelemetry receives run metadata such as evidence mix, finding IDs/severities, eval scores, Gemini status, MCP status/tool count, read-only MCP query names, and report length. When `PHOENIX_MCP_COMMAND` is configured and OTEL is live, TraceGuard starts the Phoenix MCP server over stdio, initializes a JSON-RPC session, performs `tools/list`, then attempts read-only `list-projects` and `list-traces` queries. In local no-credential mode, the app labels the Phoenix step as replay/demo output instead of pretending live trace queries happened.

The improved run shows how eval feedback changes the checklist: public access is promoted from high to critical confidence, and disabled repository controls become explicit findings instead of staying buried in raw evidence.

For hosted deployment, configure:

- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_GENAI_USE_VERTEXAI=True`
- `ENABLE_GEMINI_SYNTHESIS=true`
- `GEMINI_MODEL=gemini-2.5-flash`
- `PHOENIX_API_KEY`
- `PHOENIX_BASE_URL=https://app.phoenix.arize.com`
- `PHOENIX_COLLECTOR_ENDPOINT`
- `PHOENIX_PROJECT_NAME=traceguard-hackathon`
- `PHOENIX_MCP_SERVER=@arizeai/phoenix-mcp`
- `PHOENIX_MCP_COMMAND=phoenix-mcp`
- `PHOENIX_MCP_TIMEOUT_SECONDS=12`

The production image preinstalls `@arizeai/phoenix-mcp@4.0.13`; local experiments can still use `npx -y @arizeai/phoenix-mcp@4.0.13`.

`PHOENIX_API_KEY` is mounted from Google Secret Manager in Cloud Run. The production image includes Node/npm for the pinned Phoenix MCP command, runs as a non-root user, and exposes `/healthz` for local/container checks plus `/health` for the hosted Cloud Run URL. Google documents some Cloud Run paths ending in `z` as reserved, so the public demo uses `/health` and `/api/auth/status` for external liveness. The app returns runtime status without exposing secret values or command-line values.

## Demo Video Script

### 0:00-0:20 Problem

Cloud incidents usually arrive as a pile of logs, Terraform snippets, IAM JSON, and alert text. The report is only useful if it separates confirmed facts from guesses.

### 0:20-1:30 Live Agent Run

Load the sample bundle. Run the baseline agent. Show findings for public Cloud Run access, primitive IAM role assignment, suspicious policy/token activity, broad ingress, and credential-exfil alert text.

### 1:30-2:20 Arize / Phoenix Loop

Run the improved agent. Show the baseline-to-improved delta panel, Phoenix/OpenTelemetry status, Phoenix MCP status, and evals. Explain that eval feedback flagged under-ranked public access and missing repo security controls, so the improved run adds disabled branch protection and secret scanning findings. If the hosted runtime shows MCP `ok`, call out the discovered Phoenix tools and the read-only `list-projects` / `list-traces` query result; if not, call out the exact skipped/error reason shown in the UI.

### Agent Builder / ADK proof point

Open `traceguard/adk_agent.py` in the repo. `root_agent` is the Google ADK agent surface for Agent Builder / Agent Platform orchestration. It uses Gemini and is instructed to call `triage_evidence_tool` before making claims. The hosted Cloud Run UI is the judge-friendly runtime over the same deterministic parser/scoring/eval pipeline.

### 2:20-2:50 Final Report

Open the in-app report preview or copy/export the report. Point out evidence IDs, confirmed status, remediation, and detection logic. Emphasize that empty or malformed evidence is reported as inconclusive, not fake-clean.

### 2:50-3:00 Close

TraceGuard is meant to reduce cloud triage time without lowering the evidence bar. The agent does the repetitive analysis and leaves the final call with the human reviewer.

## Judge Notes

- The local app is dependency-free for reliable judging.
- The project uses synthetic evidence only; no third-party confidential data is included.
- The security model separates confirmed findings from hypotheses.
- The public repository includes an MIT license and all source needed to run the demo.
- Runtime badges distinguish live Gemini, Phoenix OTEL, and Phoenix MCP states from local deterministic replay mode.
