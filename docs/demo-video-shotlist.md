# TraceGuard Demo Video Shotlist

Target length: 2:45 to 3:00. The official submission video must be public on YouTube or Vimeo and linked in Devpost.

## 0:00-0:20 - The Problem

Open on the hosted TraceGuard app.

Narration:

> After a cloud incident, the security lead gets audit logs, IAM JSON, Terraform, alerts, and repo metadata. TraceGuard turns that pile into a report where a confirmed claim is not allowed unless the source evidence backs it up.

Visual receipts:

- Hosted Cloud Run URL.
- Runtime chips visible.
- 30 second story strip visible.

## 0:20-1:10 - Live Agent Run

Actions:

1. Unlock with the Devpost judge key.
2. Click `Load sample`.
3. Click `Run agent`.

Call out:

- `11` findings.
- `8` critical/high findings.
- `0` unsupported confirmed claims.
- Evidence IDs on findings.

## 1:10-1:55 - Arize Loop

Show the Arize loop panel.

Call out:

- Observe: Phoenix OTEL live and Phoenix MCP status.
- Evaluate: `94%` eval average and Gemini validation.
- Improve: baseline-to-improved finding delta.

Use this wording:

> The current loop is eval-guided and evidence-gated: Phoenix tracing and MCP make the run observable, evals show the quality gap, and the improved checklist produces a better run. The next production step is dynamic replanning from historical Phoenix trace and eval reads.

## 1:55-2:35 - Report Proof

Open the final report preview.

Call out:

- Public Cloud Run access.
- Primitive IAM role.
- Suspicious token or policy activity.
- Disabled repo controls.
- Remediation and detection fields.

## 2:35-2:55 - Google And Arize Fit

Show the repo or docs briefly.

Call out:

- Cloud Run hosted runtime.
- Gemini on Vertex AI.
- ADK-compatible `root_agent`.
- Phoenix/OpenTelemetry and Phoenix MCP.
- Local verification gate before production deploy.

## Final Upload Checklist

- Public YouTube or Vimeo link added to Devpost.
- Video under 3 minutes.
- Hosted app URL and GitHub repo URL visible in Devpost.
- Devpost judge key placed only in the private judging field.
- No secrets, cookies, API keys, or Cloud Run environment values shown on screen.
