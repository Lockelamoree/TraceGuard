from __future__ import annotations

from .models import EvalResult, EvidenceItem, Finding


def render_markdown_report(
    findings: list[Finding],
    evidence: list[EvidenceItem],
    evals: list[EvalResult],
    gemini_brief: str = "",
    metrics: dict[str, object] | None = None,
) -> str:
    evidence_by_id = {item.id: item for item in evidence}
    lines = [
        "# TraceGuard Incident Triage Report",
        "",
        "## Executive Summary",
        f"- Evidence items reviewed: {len(evidence)}",
        f"- Findings: {len(findings)}",
        "- Claim policy: confirmed findings require explicit evidence IDs.",
        "",
    ]
    if gemini_brief.strip():
        lines.extend(["## Gemini Incident Commander Brief", gemini_brief.strip(), ""])
    if metrics:
        lines.extend(
            [
                "## Run Metrics",
                f"- Duration: {metrics.get('duration_ms', 'n/a')} ms",
                f"- Critical/high findings: {metrics.get('critical_high_count', 'n/a')}",
                f"- Eval average: {metrics.get('eval_average', 'n/a')}",
                f"- Unsupported confirmed claims: {metrics.get('unsupported_confirmed_claims', 'n/a')}",
                f"- Gemini validation: {metrics.get('gemini_validation_status', 'not_run')}",
                "",
            ]
        )
    lines.append("## Findings")
    if not findings:
        lines.append("- No confirmed findings. Treat this as inconclusive, not clean.")
    for finding in findings:
        lines.extend(
            [
                f"### {finding.title}",
                f"- Severity: {finding.severity.upper()}",
                f"- Confidence: {finding.confidence:.0%}",
                f"- Status: {finding.status}",
                f"- CWE: {finding.cwe}",
                f"- MITRE ATT&CK: {', '.join(finding.mitre_attack) or 'N/A'}",
                f"- Summary: {finding.summary}",
                f"- Impact: {finding.impact}",
                f"- Remediation: {finding.remediation}",
                f"- Detection: {finding.detection}",
                f"- Evidence: {', '.join(finding.evidence_ids)}",
                "",
            ]
        )
        for evidence_id in finding.evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if item:
                lines.append(f"  - {evidence_id}: {item.detail[:280]}")
        lines.append("")
    lines.extend(["## Quality Evals"])
    for result in evals:
        lines.append(f"- {result.name}: {result.status.upper()} ({result.score}) - {result.detail}")
    lines.extend(
        [
            "",
            "## Assumptions",
            "- Evidence bundle is a safe synthetic demo unless replaced by user-provided data.",
            "- This report supports triage and should be reviewed by a human before enforcement actions.",
        ]
    )
    return "\n".join(lines)
