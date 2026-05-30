from __future__ import annotations

import json
from dataclasses import asdict

from .evals import run_evals
from .gemini_adapter import synthesize_incident_brief
from .models import AgentStep, EvalResult, EvidenceItem, Finding
from .observability import new_trace_context, trace_span
from .parsers import parse_evidence_bundle
from .phoenix_mcp import PhoenixMcpResult, inspect_phoenix_mcp
from .report import render_markdown_report
from .scoring import severity_score


def analyze_bundle(raw: str, mode: str = "improved") -> dict:
    mode = "baseline" if mode == "baseline" else "improved"
    context = new_trace_context()
    steps: list[AgentStep] = []
    span_context = {"traceguard.run_mode": mode}
    with trace_span(context, "parse_evidence", span_context) as span:
        evidence = parse_evidence_bundle(raw)
        evidence_attrs = _evidence_span_attributes(evidence)
        for key, value in evidence_attrs.items():
            span.set_attribute(key, value)
        span.add_event("traceguard.evidence.parsed", evidence_attrs)
    steps.append(AgentStep("Recon", "complete", f"Parsed {len(evidence)} evidence items from the bundle."))

    with trace_span(context, "derive_findings", span_context) as span:
        findings = derive_findings(evidence, improved=mode == "improved")
        finding_attrs = _finding_span_attributes(findings)
        for key, value in finding_attrs.items():
            span.set_attribute(key, value)
        span.add_event("traceguard.findings.derived", finding_attrs)
    steps.append(AgentStep("Enumeration", "complete", "Mapped audit, IAM, Terraform, alert, and repo signals."))
    steps.append(AgentStep("Validation", "complete", "Kept confirmed findings tied to explicit evidence IDs."))

    with trace_span(context, "run_evals", span_context) as span:
        evals = run_evals(findings, evidence)
        eval_attrs = _eval_span_attributes(evals)
        for key, value in eval_attrs.items():
            span.set_attribute(key, value)
        span.add_event("traceguard.evals.completed", eval_attrs)

    with trace_span(context, "gemini_synthesis", span_context) as span:
        gemini = synthesize_incident_brief(findings, evidence, evals, mode)
        gemini_attrs = _gemini_span_attributes(gemini)
        for key, value in gemini_attrs.items():
            span.set_attribute(key, value)
        span.add_event("traceguard.gemini.status", gemini_attrs)
    if gemini["enabled"]:
        steps.append(
            AgentStep(
                "Gemini synthesis",
                "complete" if gemini["ok"] else "warn",
                str(gemini["detail"]),
            )
        )
    with trace_span(context, "phoenix_mcp_introspection", span_context) as span:
        mcp = inspect_phoenix_mcp(context, improved=mode == "improved")
        mcp_attrs = _mcp_span_attributes(mcp)
        for key, value in mcp_attrs.items():
            span.set_attribute(key, value)
        span.add_event("traceguard.phoenix_mcp.completed", mcp_attrs)
    steps.append(AgentStep("Phoenix MCP introspection", mcp.step_status, mcp.summary))
    steps.append(AgentStep("Report", "complete", f"Generated {len(findings)} findings and {len(evals)} quality evals."))

    with trace_span(context, "render_report", span_context) as span:
        report_markdown = render_markdown_report(findings, evidence, evals, str(gemini.get("text", "")))
        report_attrs = {"traceguard.report_length": len(report_markdown)}
        span.set_attribute("traceguard.report_length", len(report_markdown))
        span.add_event("traceguard.report.rendered", report_attrs)

    return {
        "run_id": context.run_id,
        "mode": mode,
        "summary": summarize(findings),
        "steps": [asdict(step) for step in steps],
        "evidence": [asdict(item) for item in evidence],
        "findings": [finding_to_dict(finding) for finding in findings],
        "evals": [asdict(item) for item in evals],
        "gemini": gemini,
        "report_markdown": report_markdown,
        "arize": {
            "phoenix_project": context.phoenix_project,
            "phoenix_enabled": context.phoenix_enabled,
            "phoenix_collector_endpoint": context.phoenix_collector_endpoint,
            "tracing_ready": context.tracing_ready,
            "tracing_error": context.tracing_error,
            "mcp_server": context.mcp_server,
            "mcp": mcp.public_dict(),
        },
    }


def derive_findings(evidence: list[EvidenceItem], improved: bool) -> list[Finding]:
    findings: list[Finding] = []
    for item in evidence:
        detail = item.detail.lower()
        if item.kind == "iam_policy" and ("roles/owner" in detail or "roles/editor" in detail):
            findings.append(
                _finding(
                    "iam-primitive-admin",
                    "Primitive administrative IAM role assigned",
                    "high",
                    0.91,
                    item,
                    "A principal has Owner or Editor, expanding blast radius if the identity is compromised.",
                    "Replace primitive roles with least-privilege predefined or custom roles and require approval for privilege grants.",
                    'GCP Audit Logs: alert on SetIamPolicy where binding.role in ("roles/owner","roles/editor").',
                    "CWE-266",
                    ("T1098", "T1078"),
                )
            )
        if item.kind in {"iam_policy", "terraform"} and ("allusers" in detail or "allauthenticatedusers" in detail):
            severity = "critical" if improved else "high"
            confidence = 0.96 if improved else 0.82
            findings.append(
                _finding(
                    "public-cloud-resource",
                    "Public access granted to cloud resource",
                    severity,
                    confidence,
                    item,
                    "Public principals can access a protected cloud resource, creating direct data exposure or service abuse risk.",
                    "Remove public IAM members, require authenticated least-privilege access, and add policy guardrails in CI.",
                    "Detect IAM bindings containing allUsers/allAuthenticatedUsers and Terraform plans that introduce public members.",
                    "CWE-284",
                    ("T1530", "T1190"),
                )
            )
        if item.kind == "gcp_audit_log" and any(token in detail for token in ("setiampolicy", "generateaccesstoken", "serviceaccountkeys.create")):
            findings.append(
                _finding(
                    "suspicious-identity-change",
                    "Suspicious identity or policy mutation",
                    "high",
                    0.84,
                    item,
                    "A sensitive IAM or token operation occurred and should be correlated with change control and actor history.",
                    "Verify actor legitimacy, rotate exposed credentials if needed, and enforce privileged access workflow.",
                    "Alert on service account key creation, token generation, and SetIamPolicy from unusual principals or locations.",
                    "CWE-269",
                    ("T1098", "T1550"),
                )
            )
        if item.kind == "terraform" and ("0.0.0.0/0" in detail or "ingress" in detail):
            findings.append(
                _finding(
                    "broad-network-exposure",
                    "Broad network ingress exposure",
                    "medium",
                    0.78,
                    item,
                    "A service or firewall rule appears reachable from the public internet.",
                    "Restrict ingress to expected CIDRs, require IAP or private connectivity, and document temporary firewall rules.",
                    "Monitor Terraform plans and GCP firewall changes that introduce 0.0.0.0/0 ingress.",
                    "CWE-200",
                    ("T1190",),
                )
            )
        if item.kind == "alert" and any(token in detail for token in ("exfil", "credential", "token")):
            findings.append(
                _finding(
                    "credential-exfil-signal",
                    "Credential or data exfiltration signal",
                    "high",
                    0.76,
                    item,
                    "Alert text indicates possible credential exposure or data movement and needs containment validation.",
                    "Revoke affected tokens, inspect access logs, scope blast radius, and preserve evidence for incident review.",
                    "Correlate credential alerts with IAM changes, storage reads, and unusual egress volume.",
                    "CWE-522",
                    ("T1552", "T1041"),
                )
            )
        if improved and item.kind == "repo_metadata" and ("disabled" in detail):
            findings.append(
                _finding(
                    "repo-control-gap",
                    "Repository security control disabled",
                    "medium",
                    0.88,
                    item,
                    "Disabled branch protection or secret scanning weakens prevention and detection in the deployment path.",
                    "Enable branch protection, required reviews, status checks, and secret scanning before production deploys.",
                    "Alert when branch protection or secret scanning is disabled on repositories with deployment permissions.",
                    "CWE-693",
                    ("T1552", "T1195"),
                )
            )
    return sorted(_dedupe_findings(findings), key=lambda finding: severity_score(finding.severity, finding.confidence), reverse=True)


def summarize(findings: list[Finding]) -> str:
    if not findings:
        return "No confirmed findings were produced. Evidence may be incomplete or outside supported parsers."
    critical_or_high = [finding for finding in findings if finding.severity in {"critical", "high"}]
    return f"{len(findings)} findings produced, including {len(critical_or_high)} critical/high priority issues."


def finding_to_dict(finding: Finding) -> dict:
    data = asdict(finding)
    data["score"] = severity_score(finding.severity, finding.confidence)
    return data


def _evidence_span_attributes(evidence: list[EvidenceItem]) -> dict[str, object]:
    return {
        "traceguard.evidence_count": len(evidence),
        "traceguard.evidence_kinds": tuple(sorted({item.kind for item in evidence})),
    }


def _finding_span_attributes(findings: list[Finding]) -> dict[str, object]:
    return {
        "traceguard.finding_count": len(findings),
        "traceguard.finding_ids": tuple(finding.id for finding in findings),
        "traceguard.finding_severities": tuple(finding.severity for finding in findings),
        "traceguard.finding_statuses": tuple(finding.status for finding in findings),
    }


def _eval_span_attributes(evals: list[EvalResult]) -> dict[str, object]:
    attributes: dict[str, object] = {
        "traceguard.eval_count": len(evals),
        "traceguard.eval_names": tuple(result.name for result in evals),
        "traceguard.eval_scores": tuple(result.score for result in evals),
        "traceguard.eval_statuses": tuple(result.status for result in evals),
    }
    for result in evals:
        safe_name = _attribute_suffix(result.name)
        attributes[f"traceguard.eval.{safe_name}.score"] = result.score
        attributes[f"traceguard.eval.{safe_name}.status"] = result.status
    return attributes


def _gemini_span_attributes(gemini: dict[str, object]) -> dict[str, object]:
    enabled = bool(gemini.get("enabled"))
    ok = bool(gemini.get("ok"))
    return {
        "traceguard.gemini_status": "disabled" if not enabled else "ok" if ok else "warn",
        "traceguard.gemini_enabled": enabled,
        "traceguard.gemini_ok": ok,
        "traceguard.gemini_configured": bool(gemini.get("configured")),
        "traceguard.gemini_model": str(gemini.get("model", "")),
        "traceguard.gemini_detail": str(gemini.get("detail", ""))[:300],
    }


def _mcp_span_attributes(mcp: PhoenixMcpResult) -> dict[str, object]:
    return {
        "traceguard.phoenix_mcp_status": mcp.status,
        "traceguard.phoenix_mcp_attempted": mcp.attempted,
        "traceguard.phoenix_mcp_command_configured": mcp.command_configured,
        "traceguard.phoenix_mcp_tool_count": len(mcp.tool_names),
        "traceguard.phoenix_mcp_tools": mcp.tool_names,
        "traceguard.phoenix_mcp_queried_tool_count": len(mcp.queried_tool_names),
        "traceguard.phoenix_mcp_queried_tools": mcp.queried_tool_names,
        "traceguard.phoenix_mcp_resource_counts": json.dumps(mcp.resource_counts, sort_keys=True),
        "traceguard.phoenix_mcp_query_error": mcp.query_error[:300],
        "traceguard.phoenix_mcp_error": mcp.error[:300],
    }


def _attribute_suffix(name: str) -> str:
    suffix = "".join(char if char.isalnum() else "_" for char in name.lower()).strip("_")
    return suffix or "unnamed"


def _finding(
    finding_id: str,
    title: str,
    severity: str,
    confidence: float,
    item: EvidenceItem,
    impact: str,
    remediation: str,
    detection: str,
    cwe: str,
    mitre_attack: tuple[str, ...],
) -> Finding:
    return Finding(
        id=finding_id,
        title=title,
        severity=severity,
        confidence=confidence,
        status="confirmed",
        summary=f"Confirmed from {item.kind}: {item.title}.",
        impact=impact,
        remediation=remediation,
        detection=detection,
        cwe=cwe,
        evidence_ids=(item.id,),
        mitre_attack=mitre_attack,
    )


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    best: dict[tuple[str, tuple[str, ...]], Finding] = {}
    for finding in findings:
        key = (finding.id, finding.evidence_ids)
        existing = best.get(key)
        if existing is None or finding.confidence > existing.confidence:
            best[key] = finding
    return list(best.values())


def analyze_json(body: bytes) -> bytes:
    payload = json.loads(body.decode("utf-8-sig") or "{}")
    result = analyze_bundle(str(payload.get("evidence_text", "")), str(payload.get("mode", "improved")))
    return json.dumps(result).encode("utf-8")
