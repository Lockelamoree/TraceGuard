# TraceGuard Judge Evidence

This is the checklist I use to prove what TraceGuard is actually doing. The goal is to make the demo easy to verify without asking judges to trust a black box.

## Public URLs

- Hosted app: https://traceguard-cnhtsa5yrq-uc.a.run.app
- Public repository: https://github.com/Lockelamoree/Arize-track---Google-Cloud-Rapid-Agent-Hackathon-TraceGuard

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
- Local Gemini detail: `Gemini synthesis disabled; deterministic findings still produced.`
- Local Phoenix MCP status: `local_replay`.

## Hosted Verification

Use the private Devpost judge key if the hosted app prompts for access.

Suggested checks:

- `/` returns the TraceGuard UI.
- `/healthz` returns `ok`.
- `/api/auth/status` reports auth enabled and authenticated after login.
- Runtime badges clearly identify whether Gemini, Phoenix OTEL, and Phoenix MCP are live or replay/skipped.
- The final report cites evidence IDs for every confirmed finding.

## Claims Boundaries

TraceGuard does not claim exploitation, compromise, Gemini synthesis, Phoenix tracing, or MCP trace inspection unless the corresponding runtime status reports it.

Local mode is deterministic. It labels Phoenix output as replay guidance instead of implying live MCP trace queries.

The current build demonstrates an eval-guided baseline/improved replay loop. The next production step is to use Phoenix MCP trace/eval reads to generate improvement plans dynamically.
