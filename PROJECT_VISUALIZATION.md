# TraceGuard Project Visualization

I use this page as the quick architecture map. It shows what runs locally, what turns on only in the hosted path, and where each tool fits.

## End-to-End Flow

```mermaid
flowchart TD
    start["Paste or load evidence"] --> parse["Parse evidence<br/>GCP logs, IAM JSON, Terraform, alerts, repo metadata"]
    parse --> findings["Derive findings<br/>IAM risk, public exposure, token activity, ingress, repo controls"]
    findings --> score["Score risk<br/>severity, confidence, priority"]
    score --> evals["Run quality evals<br/>grounding, claim hygiene, detection, remediation, severity"]
    evals --> improve["Plan next run improvement<br/>weakest eval + MCP receipts"]
    improve --> report
    evals --> report["Render incident report<br/>evidence IDs, impact, fixes, detections"]
    report --> human["Human review and handoff"]

    findings -. "optional" .-> gemini["Gemini on Vertex AI<br/>narrative synthesis"]
    gemini -. "summarizes confirmed findings" .-> report

    findings -. "spans" .-> otel["Phoenix / OpenTelemetry"]
    evals -. "eval metrics" .-> otel
    gemini -. "model status" .-> otel
    otel --> phoenix["Phoenix project"]

    findings -. "when configured" .-> mcp["Phoenix MCP client"]
    mcp -. "initialize + tools/list + read-only trace/project query" .-> phoenix
    mcp --> improve
```

## Local vs Hosted

```mermaid
flowchart LR
    subgraph local["Local demo"]
        localEvidence["sample evidence"] --> localAgent["deterministic agent"]
        localAgent --> localEvals["quality evals"]
        localEvals --> localImprove["eval-guided improvement plan"]
        localImprove --> localReport["report preview"]
        localAgent -. "labels replay" .-> localPhoenix["Phoenix/MCP not claimed live"]
    end

    subgraph hosted["Hosted Cloud Run path"]
        hostedEvidence["judge evidence"] --> hostedAgent["TraceGuard server"]
        hostedAgent --> hostedGemini["Gemini synthesis<br/>if env vars are set"]
        hostedAgent --> hostedOtel["Phoenix OTEL spans<br/>if collector/key is set"]
        hostedAgent --> hostedMcp["Phoenix MCP tools/list + read-only trace/project query<br/>if command is set"]
        hostedMcp --> hostedImprove["observability-derived improvement plan"]
        hostedOtel --> hostedImprove
        hostedImprove --> hostedReport
        hostedGemini --> hostedReport["report preview"]
        hostedOtel --> hostedStatus["runtime badges / run steps"]
        hostedMcp --> hostedStatus
        hostedAgent --> hostedReport
    end
```

## Tool Responsibilities

| Tool or module | What I use it for | What it does not do |
| --- | --- | --- |
| `traceguard/parsers.py` | Turns mixed evidence into structured records. | It does not infer compromise on its own. |
| `traceguard/agent.py` | Orchestrates parsing, findings, evals, Gemini, MCP, and reporting. | It does not let Gemini create findings from scratch. |
| `traceguard/evals.py` | Checks grounding, claim hygiene, detection quality, remediation quality, severity, and duplicates. | It does not replace human review. |
| `traceguard/improvement.py` | Converts the weakest eval plus Phoenix MCP read-query receipts into a next-run change. | It does not self-modify production code during a judge run. |
| `traceguard/gemini_adapter.py` | Adds optional hosted narrative synthesis through Gemini and rejects live briefs that do not cite known evidence IDs. | It is disabled locally unless Google Cloud env vars are configured, and it does not create findings. |
| `traceguard/observability.py` | Sends Phoenix/OpenTelemetry spans when configured. | It does not claim live tracing in local replay mode. |
| `traceguard/phoenix_mcp.py` | Starts a pinned Phoenix MCP command, performs `initialize` + `tools/list`, then attempts read-only `list-projects` and `list-traces`. | It does not expose secrets or mutate Phoenix data. |
| `traceguard/report.py` | Produces the markdown incident report. | It does not remove evidence IDs from confirmed findings. |
| `web/` | Gives judges and reviewers a small UI for loading evidence, running baseline/improved, checking the proof scoreboard, and copying the report. | It is not a SIEM replacement. |

## Current Boundary

The current build demonstrates an eval-guided baseline/improved loop, read-only Phoenix MCP trace/project querying when Phoenix is live, and an improvement planner that turns those receipts into a concrete next-run change. It uses observability and eval outputs directly, but it does not claim autonomous production self-modification.
