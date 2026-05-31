const evidence = document.querySelector("#evidence");
const summary = document.querySelector("#summary");
const runtimeDetail = document.querySelector("#runtimeDetail");
const proofScoreboard = document.querySelector("#proofScoreboard");
const arizeLoop = document.querySelector("#arizeLoop");
const geminiBrief = document.querySelector("#geminiBrief");
const steps = document.querySelector("#steps");
const findings = document.querySelector("#findings");
const evals = document.querySelector("#evals");
const findingCount = document.querySelector("#findingCount");
const deltaStatus = document.querySelector("#deltaStatus");
const deltaPanel = document.querySelector("#deltaPanel");
const reportStatus = document.querySelector("#reportStatus");
const reportPreview = document.querySelector("#reportPreview");
const copyReportButton = document.querySelector("#copyReport");
const runtimeStatus = document.querySelector("#runtimeStatus");
const authGate = document.querySelector("#authGate");
const appShell = document.querySelector("#appShell");
const authForm = document.querySelector("#authForm");
const authToken = document.querySelector("#authToken");
const authMessage = document.querySelector("#authMessage");
const logoutButton = document.querySelector("#logoutButton");
let lastReport = "";
const runState = {
  evidenceText: "",
  baseline: null,
  improved: null,
};
const actionButtons = ["#loadSample", "#runBaseline", "#runImproved"]
  .map((selector) => document.querySelector(selector));

renderDelta();
renderReportPreview("");
renderProofScoreboard(null);
renderArizeLoop(null);
initialize();

document.querySelector("#loadSample").addEventListener("click", async () => {
  const response = await fetchWithAuth("/sample");
  if (!response) return;
  evidence.value = await response.text();
  resetRunState();
  summary.textContent = "Sample loaded. Run the improved agent to capture the baseline and improved delta.";
});

document.querySelector("#runBaseline").addEventListener("click", () => runAgent("baseline"));
document.querySelector("#runImproved").addEventListener("click", () => runAgent("improved"));
evidence.addEventListener("input", resetRunState);
copyReportButton.addEventListener("click", async () => {
  if (!lastReport) return;
  await navigator.clipboard.writeText(lastReport);
  summary.textContent = "Report copied.";
});
authForm.addEventListener("submit", login);
logoutButton.addEventListener("click", logout);

async function runAgent(mode) {
  ensureRunStateForEvidence();
  setBusy(true);
  try {
    if (mode === "improved" && !runState.baseline) {
      summary.textContent = "Running baseline comparison first...";
      const baseline = await requestAnalysis("baseline");
      if (!baseline) return;
      rememberRun(baseline);
      renderDelta();
    }

    summary.textContent = mode === "baseline" ? "Running baseline triage..." : "Running improved triage...";
    const result = await requestAnalysis(mode);
    if (!result) return;
    render(result);
  } finally {
    setBusy(false);
  }
}

async function requestAnalysis(mode) {
  const response = await fetchWithAuth("/api/analyze", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ evidence_text: evidence.value, mode }),
  });
  if (!response) return null;
  const result = await response.json().catch(() => ({ error: "Agent returned invalid JSON." }));
  if (!response.ok || result.error) {
    summary.textContent = result.error || "Agent run failed.";
    return null;
  }
  return result;
}

async function initialize() {
  try {
    const status = await fetch("/api/auth/status").then((response) => response.json());
    if (status.enabled && !status.authenticated) {
      showAuthGate("");
      return;
    }
    showApp(status.enabled);
    await loadRuntimeStatus();
  } catch {
    showApp(false);
    runtimeStatus.textContent = "Runtime status unavailable";
  }
}

async function login(event) {
  event.preventDefault();
  authMessage.textContent = "Checking key...";
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token: authToken.value }),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || !result.authenticated) {
    authMessage.textContent = result.error || "Invalid access key.";
    authToken.select();
    return;
  }
  authToken.value = "";
  authMessage.textContent = "";
  showApp(result.enabled);
  await loadRuntimeStatus();
}

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  showAuthGate("Locked.");
}

async function fetchWithAuth(url, options) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    showAuthGate("Session expired.");
    return null;
  }
  return response;
}

function showAuthGate(message) {
  appShell.classList.add("hidden");
  authGate.classList.remove("hidden");
  logoutButton.classList.add("hidden");
  authMessage.textContent = message;
  window.setTimeout(() => authToken.focus(), 0);
}

function showApp(authEnabled) {
  authGate.classList.add("hidden");
  appShell.classList.remove("hidden");
  logoutButton.classList.toggle("hidden", !authEnabled);
}

async function loadRuntimeStatus() {
  try {
    const response = await fetchWithAuth("/api/runtime");
    if (!response) return;
    const status = await response.json();
    const geminiState = status.enable_gemini_synthesis && status.google_cloud_project ? "Gemini configured" : "Gemini local deterministic";
    const phoenixState = status.phoenix_api_key_configured || status.phoenix_collector_endpoint ? "Phoenix configured" : "Phoenix local trace context";
    const mcpState = status.phoenix_mcp_command_configured ? "MCP command ready" : "MCP command unset";
    const authState = status.traceguard_auth_configured ? "Auth on" : "Auth local-off";
    setRuntimeStatus([
      { label: authState, tone: status.traceguard_auth_configured ? "good" : "info" },
      { label: geminiState, tone: status.enable_gemini_synthesis && status.google_cloud_project ? "info" : "neutral" },
      { label: phoenixState, tone: status.phoenix_api_key_configured || status.phoenix_collector_endpoint ? "info" : "neutral" },
      { label: mcpState, tone: status.phoenix_mcp_command_configured ? "info" : "neutral" },
    ]);
    runtimeDetail.textContent = [
      status.enable_gemini_synthesis && status.google_cloud_project
        ? "Gemini is configured; live synthesis is confirmed only after a successful run."
        : "Gemini is not live; deterministic local findings and reports are still produced.",
      status.phoenix_api_key_configured || status.phoenix_collector_endpoint
        ? "Phoenix config is present; hosted trace delivery is confirmed only when a run reports OTEL live."
        : "Phoenix is not configured; the UI shows local trace context and eval output only.",
      status.phoenix_mcp_command_configured
        ? "Phoenix MCP command is configured; read-only tool discovery and trace/project queries are attempted when OTEL tracing is live."
        : "Phoenix MCP command is unset; live MCP queries are skipped instead of being implied.",
    ].join(" ");
  } catch {
    runtimeStatus.textContent = "Runtime status unavailable";
    runtimeDetail.textContent = "Runtime status unavailable. Local deterministic analysis may still work if the API is reachable.";
  }
}

function render(result) {
  rememberRun(result);
  lastReport = result.report_markdown || "";
  renderRuntimeFromResult(result);
  renderProofScoreboard(result);
  renderArizeLoop(result);
  renderReportPreview(lastReport, result.mode);
  renderDelta();
  summary.textContent = `${result.summary} Run mode: ${result.mode}.`;
  geminiBrief.innerHTML = `
    <strong>${escapeHtml(result.gemini?.provider || "Google Cloud Gemini")}</strong>
    <div class="detail">${escapeHtml(result.gemini?.detail || "Gemini synthesis not configured.")}</div>
    ${result.gemini?.text ? `<div class="detail generated">${escapeHtml(result.gemini.text)}</div>` : ""}
  `;
  steps.innerHTML = result.steps.map((step) => `
    <article class="step">
      <strong>${escapeHtml(step.name)} - ${escapeHtml(step.status)}</strong>
      <div class="detail">${escapeHtml(step.detail)}</div>
    </article>
  `).join("");
  findingCount.textContent = `${result.findings.length} findings`;
  findings.innerHTML = result.findings.map((finding) => `
    <article class="finding">
      <strong><span class="${finding.severity}">${finding.severity.toUpperCase()}</span> - ${escapeHtml(finding.title)}</strong>
      <div class="meta">
        <span class="badge">confidence ${Math.round(finding.confidence * 100)}%</span>
        <span class="badge">score ${finding.score}</span>
        <span class="badge">${escapeHtml(finding.cwe)}</span>
        <span class="badge">evidence ${finding.evidence_ids.join(", ")}</span>
      </div>
      <div class="detail">${escapeHtml(finding.impact)}</div>
      <div class="detail"><strong>Fix</strong>: ${escapeHtml(finding.remediation)}</div>
      <div class="detail"><strong>Detect</strong>: ${escapeHtml(finding.detection)}</div>
    </article>
  `).join("") || `<div class="detail">No confirmed findings. Treat that as inconclusive, not automatically clean.</div>`;
  evals.innerHTML = result.evals.map((item) => `
    <article class="eval">
      <strong><span class="${item.status}">${item.status.toUpperCase()}</span> - ${escapeHtml(item.name)}</strong>
      <div class="meta"><span class="badge">score ${item.score}</span></div>
      <div class="detail">${escapeHtml(item.detail)}</div>
    </article>
  `).join("");
}

function resetRunState() {
  runState.evidenceText = evidence.value;
  runState.baseline = null;
  runState.improved = null;
  lastReport = "";
  renderReportPreview("");
  renderProofScoreboard(null);
  renderArizeLoop(null);
  renderDelta();
}

function ensureRunStateForEvidence() {
  if (runState.evidenceText !== evidence.value) {
    resetRunState();
  }
}

function rememberRun(result) {
  ensureRunStateForEvidence();
  if (result.mode === "baseline") {
    runState.baseline = result;
  }
  if (result.mode === "improved") {
    runState.improved = result;
  }
}

function setBusy(isBusy) {
  actionButtons.forEach((button) => {
    button.disabled = isBusy;
  });
}

function renderRuntimeFromResult(result) {
  const geminiState = result.gemini?.ok
    ? "Gemini live"
    : result.gemini?.enabled
      ? "Gemini configured, not live"
      : "Gemini deterministic local";
  const phoenixState = result.arize?.tracing_ready
    ? "Phoenix OTEL live"
    : result.arize?.phoenix_enabled
      ? "Phoenix configured, not live"
      : "Phoenix local trace context";
  const mcpState = result.arize?.mcp?.status === "ok"
    ? "MCP live query"
    : result.arize?.mcp?.status === "discovery_only"
      ? "MCP discovery live"
    : result.arize?.mcp?.command_configured
      ? "MCP attempted"
      : "MCP skipped";
  setRuntimeStatus([
    { label: geminiState, tone: result.gemini?.ok ? "good" : result.gemini?.enabled ? "warn" : "neutral" },
    { label: phoenixState, tone: result.arize?.tracing_ready ? "good" : result.arize?.phoenix_enabled ? "warn" : "neutral" },
    {
      label: mcpState,
      tone: result.arize?.mcp?.status === "ok"
        ? "good"
        : result.arize?.mcp?.status === "discovery_only"
          ? "info"
          : result.arize?.mcp?.command_configured
            ? "warn"
            : "neutral",
    },
  ]);

  const geminiDetail = result.gemini?.ok
    ? `Gemini generated a live Vertex AI brief with ${escapeHtml(result.gemini.model || "the configured model")}.`
    : result.gemini?.enabled
      ? `Gemini is enabled, but this run did not produce a live brief: ${escapeHtml(result.gemini.detail || "no detail returned")}.`
      : "Gemini is disabled for this run; report content is deterministic local output.";
  const phoenixDetail = result.arize?.tracing_ready
    ? `Phoenix OTEL is live for project ${escapeHtml(result.arize.phoenix_project || "traceguard")}.`
    : result.arize?.phoenix_enabled
      ? `Phoenix config is present, but hosted trace delivery was not confirmed${result.arize.tracing_error ? `: ${escapeHtml(result.arize.tracing_error)}` : "."}`
      : "Phoenix is not configured; showing local trace context and evals only.";
  const mcpDetail = result.arize?.mcp?.summary
    ? escapeHtml(result.arize.mcp.summary)
    : "Phoenix MCP introspection status was not returned.";
  const mcpQueries = Array.isArray(result.arize?.mcp?.queried_tool_names) && result.arize.mcp.queried_tool_names.length
    ? `Read-only MCP queries: ${escapeHtml(result.arize.mcp.queried_tool_names.join(", "))}.`
    : "No read-only Phoenix trace/project query completed in this run.";
  const metrics = result.metrics || {};
  const runReceipt = `Run ${escapeHtml(result.run_id || "unavailable")} completed in ${escapeHtml(metrics.duration_ms || "n/a")} ms.`;
  const phoenixProject = result.arize?.phoenix_project
    ? `Phoenix project: ${escapeHtml(result.arize.phoenix_project)}.`
    : "Phoenix project not returned.";
  const geminiModel = result.gemini?.model
    ? `Gemini model: ${escapeHtml(result.gemini.model)}.`
    : "Gemini model not returned.";

  runtimeDetail.innerHTML = `
    <strong>Runtime</strong>
    <div class="detail">${runReceipt}</div>
    <div class="detail">${phoenixProject}</div>
    <div class="detail">${geminiModel}</div>
    <div class="detail">${geminiDetail}</div>
    <div class="detail">${phoenixDetail}</div>
    <div class="detail">${mcpDetail}</div>
    <div class="detail">${mcpQueries}</div>
  `;
}

function renderReportPreview(report, mode = "") {
  reportPreview.textContent = report || "Run the improved agent to preview the generated markdown report here.";
  reportStatus.textContent = report ? `${formatMode(mode)} report` : "No report yet";
  copyReportButton.disabled = !report;
}

function renderProofScoreboard(result) {
  if (!result) {
    proofScoreboard.innerHTML = `
      ${renderScoreCard("Run receipt", "Pending", "Run the agent")}
      ${renderScoreCard("Cited claims", "Pending", "Evidence IDs required")}
      ${renderScoreCard("Eval receipt", "Pending", "Quality checks")}
      ${renderScoreCard("Phoenix MCP", "Pending", "Live status after run")}
    `;
    return;
  }

  const metrics = result.metrics || {};
  const duration = Number(metrics.duration_ms || 0);
  const evalAverage = Number(metrics.eval_average || avgEvalScore(result.evals));
  const unsupported = Number(metrics.unsupported_confirmed_claims || 0);
  const mcp = result.arize?.mcp || {};
  const mcpLabel = mcp.status === "ok"
    ? `${Number(mcp.queried_tool_count || 0)} queries`
    : mcp.status === "discovery_only"
      ? `${Number(mcp.tool_count || 0)} tools`
      : mcp.status || "skipped";
  const geminiValidation = metrics.gemini_validation_status || result.gemini?.validation_status || "not_run";

  proofScoreboard.innerHTML = `
    ${renderScoreCard("Run receipt", duration ? `${duration} ms` : "Measured", "End-to-end agent run")}
    ${renderScoreCard("Cited claims", `${unsupported} unsupported`, "Confirmed claims")}
    ${renderScoreCard("Eval receipt", `${Math.round(evalAverage * 100)}%`, "Report quality")}
    ${renderScoreCard("Gemini", geminiValidation, "Evidence-ID validator")}
    ${renderScoreCard("Phoenix MCP", mcpLabel, "Read-only path")}
    ${renderScoreCard("Critical/high", Number(metrics.critical_high_count ?? countCriticalHigh(result.findings)), "Priority findings")}
  `;
}

function renderArizeLoop(result) {
  if (!result) {
    arizeLoop.innerHTML = `
      <article class="loop-card loop-receipt pending">
        <span>Improvement receipt</span>
        <strong>Phoenix trace/eval -> checklist change -> better run</strong>
        <em>Run the agent to populate the live proof chain.</em>
      </article>
      <article class="loop-card pending">
        <span>01 Observe</span>
        <strong>Phoenix pending</strong>
        <em>Run the agent to verify OTEL and MCP status.</em>
      </article>
      <article class="loop-card pending">
        <span>02 Evaluate</span>
        <strong>Evals pending</strong>
        <em>Grounding and report checks appear after analysis.</em>
      </article>
      <article class="loop-card pending">
        <span>03 Improve</span>
        <strong>Delta pending</strong>
        <em>Baseline vs improved coverage is shown after both runs.</em>
      </article>
    `;
    return;
  }

  const metrics = result.metrics || {};
  const mcp = result.arize?.mcp || {};
  const otelLive = Boolean(result.arize?.tracing_ready);
  const queried = Array.isArray(mcp.queried_tool_names) ? mcp.queried_tool_names : [];
  const evalAverage = Number(metrics.eval_average || avgEvalScore(result.evals));
  const unsupported = Number(metrics.unsupported_confirmed_claims || 0);
  const baseline = runState.baseline;
  const improved = runState.improved || (result.mode === "improved" ? result : null);
  const improvedOnly = baseline && improved
    ? improved.findings.filter((finding) => !findingMap(baseline.findings).has(findingKey(finding)))
    : [];
  const findingGain = baseline && improved ? improved.findings.length - baseline.findings.length : 0;
  const queriedCount = queried.length || Number(mcp.queried_tool_count || 0);
  const mcpProof = mcp.status === "ok"
    ? `${Number(mcp.tool_count || 0)} tools, ${queriedCount} read query`
    : mcp.status === "discovery_only"
      ? `${Number(mcp.tool_count || 0)} tools discovered`
      : mcp.status || "MCP not run";
  const improvementReceipt = baseline && improved
    ? `${otelLive ? "Phoenix OTEL live" : "Trace context shown"}; eval avg ${Math.round(evalAverage * 100)}%; ${formatSigned(findingGain)} findings in improved run.`
    : "Baseline and improved runs appear here as one proof chain.";

  arizeLoop.innerHTML = `
    <article class="loop-card loop-receipt ${baseline && improved ? "good" : "warn"}">
      <span>Improvement receipt</span>
      <strong>Phoenix trace/eval -> checklist change -> better run</strong>
      <em>${escapeHtml(improvementReceipt)}</em>
    </article>
    <article class="loop-card ${otelLive ? "good" : "warn"}">
      <span>01 Observe</span>
      <strong>${otelLive ? "Phoenix OTEL live" : result.arize?.phoenix_enabled ? "Phoenix configured" : "Local replay"}</strong>
      <em>${escapeHtml(mcpProof)}</em>
    </article>
    <article class="loop-card ${unsupported === 0 ? "good" : "warn"}">
      <span>02 Evaluate</span>
      <strong>${Math.round(evalAverage * 100)}% eval avg</strong>
      <em>${unsupported} unsupported confirmed claims; Gemini ${escapeHtml(metrics.gemini_validation_status || result.gemini?.validation_status || "not_run")}.</em>
    </article>
    <article class="loop-card ${baseline && improved ? "good" : "warn"}">
      <span>03 Improve</span>
      <strong>${baseline && improved ? `${formatSigned(findingGain)} findings` : "Baseline pending"}</strong>
      <em>${baseline && improved ? improvementSummary(improvedOnly) : "Run improved to show the baseline-to-improved loop."}</em>
    </article>
  `;
}

function improvementSummary(improvedOnly) {
  if (improvedOnly.length) {
    const findingIds = [...new Set(improvedOnly.map((finding) => finding.id))];
    const evidenceIds = [...new Set(improvedOnly.flatMap((finding) => finding.evidence_ids || []))];
    return `Improved coverage: ${findingIds.join(", ")} with evidence ${evidenceIds.join(", ")}.`;
  }
  return "Improved run preserves evidence-grounded coverage.";
}

function formatSigned(value) {
  return value > 0 ? `+${value}` : String(value);
}

function renderScoreCard(label, value, detail) {
  return `
    <article class="score-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <em>${escapeHtml(detail)}</em>
    </article>
  `;
}

function renderDelta() {
  const baseline = runState.baseline;
  const improved = runState.improved;

  if (!baseline && !improved) {
    deltaStatus.textContent = "Waiting for runs";
    deltaPanel.innerHTML = `
      <div class="delta-empty">
        Run the improved agent to capture a baseline first, then compare finding coverage, risk scoring, and eval quality.
      </div>
    `;
    return;
  }

  if (!baseline || !improved) {
    const captured = baseline || improved;
    deltaStatus.textContent = baseline ? "Improved pending" : "Baseline pending";
    deltaPanel.innerHTML = `
      <div class="delta-empty">
        ${escapeHtml(formatMode(captured.mode))} captured. ${baseline ? "Run improved to complete the delta." : "Run baseline to complete the delta."}
      </div>
      <div class="delta-metrics">
        ${renderSnapshotTile("Findings", captured.findings.length)}
        ${renderSnapshotTile("Critical/high", countCriticalHigh(captured.findings))}
        ${renderSnapshotTile("Top score", topRiskScore(captured.findings))}
        ${renderSnapshotTile("Eval avg", `${Math.round(avgEvalScore(captured.evals) * 100)}%`)}
      </div>
    `;
    return;
  }

  const baselineMetrics = resultMetrics(baseline);
  const improvedMetrics = resultMetrics(improved);
  const improvedOnly = improved.findings.filter((finding) => !findingMap(baseline.findings).has(findingKey(finding)));
  const baselineByKey = findingMap(baseline.findings);
  const severityUpgrades = improved.findings.filter((finding) => {
    const previous = baselineByKey.get(findingKey(finding));
    return previous && severityRank(finding.severity) > severityRank(previous.severity);
  });
  const scoreGains = improved.findings
    .map((finding) => {
      const previous = baselineByKey.get(findingKey(finding));
      return previous ? { finding, delta: Number(finding.score || 0) - Number(previous.score || 0) } : null;
    })
    .filter((item) => item && item.delta > 0)
    .sort((left, right) => right.delta - left.delta)
    .slice(0, 2);

  deltaStatus.textContent = "Comparison ready";
  deltaPanel.innerHTML = `
    <div class="delta-metrics">
      ${renderDeltaTile("Findings", baselineMetrics.findings, improvedMetrics.findings)}
      ${renderDeltaTile("Critical/high", baselineMetrics.criticalHigh, improvedMetrics.criticalHigh)}
      ${renderDeltaTile("Top score", baselineMetrics.topScore, improvedMetrics.topScore)}
      ${renderDeltaTile("Eval avg", baselineMetrics.avgEval, improvedMetrics.avgEval, (value) => `${Math.round(value * 100)}%`)}
    </div>
    <div class="delta-notes">
      ${renderDeltaNote(
        "Improved-only coverage",
        improvedOnly.length
          ? improvedOnly.map((finding) => `${finding.id} (${finding.severity}, score ${finding.score})`).join("; ")
          : "No improved-only finding IDs."
      )}
      ${renderDeltaNote(
        "Severity changes",
        severityUpgrades.length
          ? severityUpgrades.map((finding) => `${finding.id} moved to ${finding.severity}`).join("; ")
          : "No severity upgrades across shared evidence IDs."
      )}
      ${renderDeltaNote(
        "Risk score gains",
        scoreGains.length
          ? scoreGains.map(({ finding, delta }) => `${finding.id} +${delta}`).join("; ")
          : "No score gains on shared findings."
      )}
    </div>
  `;
}

function resultMetrics(result) {
  return {
    findings: result.findings.length,
    criticalHigh: countCriticalHigh(result.findings),
    topScore: topRiskScore(result.findings),
    avgEval: avgEvalScore(result.evals),
  };
}

function renderSnapshotTile(label, value) {
  return `
    <div class="delta-tile">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function renderDeltaTile(label, baselineValue, improvedValue, formatter = (value) => value) {
  const delta = improvedValue - baselineValue;
  const formattedDelta = delta > 0 ? `+${formatter(delta)}` : formatter(delta);
  return `
    <div class="delta-tile">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(formatter(baselineValue))} -> ${escapeHtml(formatter(improvedValue))}</strong>
      <em class="${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}">${escapeHtml(formattedDelta)}</em>
    </div>
  `;
}

function renderDeltaNote(label, value) {
  return `
    <div class="delta-note">
      <strong>${escapeHtml(label)}</strong>
      <span>${escapeHtml(value)}</span>
    </div>
  `;
}

function countCriticalHigh(items) {
  return items.filter((finding) => ["critical", "high"].includes(finding.severity)).length;
}

function topRiskScore(items) {
  return items.reduce((max, finding) => Math.max(max, Number(finding.score) || 0), 0);
}

function avgEvalScore(items) {
  if (!items.length) return 0;
  return items.reduce((total, item) => total + (Number(item.score) || 0), 0) / items.length;
}

function findingMap(items) {
  return new Map(items.map((finding) => [findingKey(finding), finding]));
}

function findingKey(finding) {
  return `${finding.id}:${(finding.evidence_ids || []).join(",")}`;
}

function severityRank(severity) {
  return { info: 1, low: 2, medium: 3, high: 4, critical: 5 }[String(severity).toLowerCase()] || 0;
}

function formatMode(mode) {
  return mode ? `${mode.charAt(0).toUpperCase()}${mode.slice(1)}` : "Latest";
}

function setRuntimeStatus(items) {
  runtimeStatus.innerHTML = items.map(({ label, tone }) => (
    `<span class="runtime-chip ${escapeHtml(tone || "neutral")}">${escapeHtml(label)}</span>`
  )).join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
