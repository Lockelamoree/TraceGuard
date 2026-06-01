# TraceGuard Judge Evidence

This is the checklist I use to prove what TraceGuard is actually doing. The goal is to make the demo easy to verify without asking judges to trust a black box.

## Public URLs

- Hosted app: https://traceguard-cnhtsa5yrq-uc.a.run.app
- Public repository: https://github.com/Lockelamoree/TraceGuard
- Public proof endpoint: https://traceguard-cnhtsa5yrq-uc.a.run.app/proof
- Sanitized hosted live proof: [docs/hosted-live-proof.md](docs/hosted-live-proof.md)

## Local Verification

Run:

```powershell
python -m traceguard.server --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000`.

Expected deterministic demo path:

1. Click `Load sample`.
2. Click `Baseline`.
3. Click `Run agent`.
4. Confirm the final report preview is populated.

Expected local outputs:

- Baseline summary: `9 findings produced, including 8 critical/high priority issues.`
- Improved summary: `11 findings produced, including 8 critical/high priority issues.`
- Proof scoreboard on the included sample: `10` evidence items, `11` findings, `8` critical/high findings, eval average around `0.94`, and `0` unsupported confirmed claims.
- Local Gemini detail: `Gemini synthesis disabled; deterministic findings still produced.`
- Local Phoenix MCP status: `local_replay`.
- Local improvement plan: `eval_guided_local`, sourced from code eval receipts. On the sample bundle, duplicate-pressure is the weakest eval and the next-run change recommends clustering repeated findings while preserving every evidence ID.

Screenshot proof:

![TraceGuard local proof scoreboard](docs/screenshots/traceguard-local-proof.png)

Hosted public screenshot:

![TraceGuard hosted public sample selector](docs/screenshots/traceguard-hosted-gemini3-workbench.png)

Hosted proof crops:

![TraceGuard hosted runtime badges](docs/screenshots/traceguard-hosted-gemini3-runtime-badges.png)

![TraceGuard hosted proof scoreboard](docs/screenshots/traceguard-hosted-gemini3-proof-scoreboard.png)

![TraceGuard hosted Arize improvement loop](docs/screenshots/traceguard-hosted-gemini3-arize-loop.png)

![TraceGuard hosted improvement delta](docs/screenshots/traceguard-hosted-improvement-delta.png)

![TraceGuard hosted report evidence](docs/screenshots/traceguard-hosted-gemini3-report-evidence.png)

Latest local verification I ran on May 31, 2026:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Result: `38` tests passed. In my local Codex shell, `python` and `py -3.11` were not on PATH, so I ran the same command with the bundled Python runtime. That does not change the app requirement; a normal Python 3.11+ install can run the suite.

Latest hosted verification I ran on June 1, 2026:

- Cloud Run service describe reported the latest ready revision serving `100%` of traffic.
- `/health` returned `200`.
- `HEAD /` and `HEAD /proof` returned `200`.
- `/proof` returned a public non-secret receipt with `project=TraceGuard`, auth disabled for judging, `secrets_exposed=false`, Gemini 3 Flash Preview model configuration, and a sanitized `latest_run` receipt.
- `/api/auth/status` returned auth disabled and authenticated for public judging.
- Public sample run returned `10` evidence items, `11` findings, `8` critical/high findings, `0` unsupported confirmed claims, `0.94` eval average, Gemini 3 validation `pass` with `0` rejected evidence references, Phoenix tracing ready, Phoenix MCP `ok`, `27` MCP tools, and one read-only `list-traces` query path.

The sanitized proof is in [docs/hosted-live-proof.md](docs/hosted-live-proof.md).

## Rubric Mapping

| Rubric area | TraceGuard evidence |
| --- | --- |
| Technological implementation | Cloud Run app, Gemini on Vertex AI, ADK `root_agent`, Phoenix OTEL, Phoenix MCP, TraceGuard code evals, observability-derived improvement planner, public hosted judging demo. |
| Design | Judge proof scoreboard, runtime badges, Arize loop panel, improvement receipt, final report preview with evidence IDs. |
| Potential impact | Shortens cloud security triage while preserving confirmed/hypothesis boundaries for human reviewers. |
| Quality of idea | Evidence-gated incident-report agent with observability-backed eval loop instead of unsupported AI summaries. |

## Hosted Verification

The hosted judging app is public. Judges do not need an access key.

Suggested checks:

- `/` returns the TraceGuard UI.
- `/health` returns `ok` on the hosted Cloud Run URL.
- Local/container `/healthz` returns `ok`; Cloud Run's public `run.app` URL reserves some paths ending in `z`, so hosted `/healthz` can return a Google Frontend 404 before it reaches the container.
- `/api/auth/status` reports auth disabled and authenticated for public judging.
- Runtime badges clearly identify whether Gemini, Phoenix OTEL, and Phoenix MCP are live or replay/skipped.
- If Phoenix MCP is live, the runtime detail reports discovered tools and read-only `list-projects` / `list-traces` query status.
- The Arize loop panel should show `Phoenix OTEL live`, MCP tool discovery/read-query proof, eval average, unsupported confirmed claim count, Gemini validation, and the next-run improvement plan.
- The proof scoreboard reports runtime duration, eval average, unsupported confirmed claims, Gemini validation status, MCP status, and critical/high count.
- The final report cites evidence IDs for every confirmed finding and includes an `Observability Improvement Plan` section with eval/MCP receipts.

## Claims Boundaries

TraceGuard does not claim exploitation, compromise, Gemini synthesis, Phoenix tracing, or MCP trace inspection unless the corresponding runtime status reports it.

Local mode is deterministic. It labels Phoenix output as replay guidance instead of implying live MCP trace queries.

The current build demonstrates an eval-guided baseline/improved loop plus a dynamic improvement planner. TraceGuard runs the code evals. Phoenix/OpenTelemetry observes the run, and Phoenix MCP provides read-only trace/project receipts that can mark the plan `observability_derived`; without Phoenix credentials, it falls back to `eval_guided_local` and says so. TraceGuard recommends the next checklist/reporting change from receipts, but it does not self-modify production code during a judge run.
