from __future__ import annotations

from dataclasses import dataclass

from .models import EvalResult, EvidenceItem, Finding
from .phoenix_mcp import PhoenixMcpResult


@dataclass(frozen=True)
class ImprovementPlan:
    status: str
    source: str
    observation: str
    eval_signal: str
    mcp_signal: str
    recommendation: str
    next_run_change: str
    receipts: tuple[str, ...]


def plan_observability_improvement(
    evals: list[EvalResult],
    findings: list[Finding],
    evidence: list[EvidenceItem],
    mcp: PhoenixMcpResult,
) -> ImprovementPlan:
    """Turn eval and Phoenix MCP receipts into a concrete next-run improvement."""
    if not evals:
        return ImprovementPlan(
            status="no_evals",
            source=_source_from_mcp(mcp),
            observation="No quality evals were available to plan an improvement.",
            eval_signal="eval:none",
            mcp_signal=_mcp_signal(mcp),
            recommendation="Run quality evals before changing detector or reporting behavior.",
            next_run_change="Keep the current checklist unchanged until eval receipts exist.",
            receipts=_receipts(mcp, (), findings, evidence),
        )

    weakest = min(evals, key=lambda item: (item.score, item.name))
    recommendation, next_run_change = _recommendation_for_eval(weakest)
    status = _status_from_mcp(mcp)
    observation = (
        f"Lowest eval signal is {weakest.name} at {weakest.score:.2f} ({weakest.status}); "
        f"{len(findings)} findings were produced from {len(evidence)} evidence items."
    )
    return ImprovementPlan(
        status=status,
        source=_source_from_mcp(mcp),
        observation=observation,
        eval_signal=f"eval:{weakest.name}={weakest.score:.2f}:{weakest.status}",
        mcp_signal=_mcp_signal(mcp),
        recommendation=recommendation,
        next_run_change=next_run_change,
        receipts=_receipts(mcp, (weakest,), findings, evidence),
    )


def _recommendation_for_eval(eval_result: EvalResult) -> tuple[str, str]:
    recommendations = {
        "risk_signal_coverage": (
            "Use eval coverage gaps to prioritize the next detector before adding new report prose.",
            "Compare expected risk IDs against actual finding IDs, then add the missing detector or sample fixture.",
        ),
        "evidence_grounding": (
            "Tighten the evidence gate before accepting new confirmed claims.",
            "Reject or downgrade findings that cite unknown evidence IDs before the report is rendered.",
        ),
        "confirmed_claim_hygiene": (
            "Preserve the confirmed-versus-hypothesis boundary in every generated artifact.",
            "Route weakly supported claims to hypothesis language until an explicit evidence ID exists.",
        ),
        "detection_usefulness": (
            "Improve operator handoff by filling missing detection logic first.",
            "Require every confirmed finding to include a SIEM or cloud-log detection opportunity.",
        ),
        "remediation_usefulness": (
            "Improve fix quality before widening detector coverage.",
            "Require every confirmed finding to include a concrete owner-actionable remediation.",
        ),
        "severity_calibration": (
            "Use eval feedback to recalibrate public-exposure severity.",
            "Promote public allUsers/allAuthenticatedUsers exposure to critical when the evidence supports direct access.",
        ),
        "duplicate_pressure": (
            "Reduce repeated detector pressure without losing cited evidence.",
            "Cluster repeated findings by detector and incident theme, while preserving every contributing evidence ID.",
        ),
        "false_positive_control": (
            "Protect low-signal bundles from noisy findings.",
            "Run negative-control samples before expanding detector patterns, and keep empty output explicitly inconclusive.",
        ),
    }
    return recommendations.get(
        eval_result.name,
        (
            "Use the weakest eval as the next checklist update target.",
            f"Create a focused regression test for {eval_result.name} before changing triage logic.",
        ),
    )


def _status_from_mcp(mcp: PhoenixMcpResult) -> str:
    if mcp.status == "ok" and mcp.queried_tool_names:
        return "observability_derived"
    if mcp.status == "discovery_only":
        return "mcp_discovery_guided"
    return "eval_guided_local"


def _source_from_mcp(mcp: PhoenixMcpResult) -> str:
    if mcp.status == "ok" and mcp.queried_tool_names:
        return "phoenix_mcp_read_queries+code_evals"
    if mcp.status == "discovery_only":
        return "phoenix_mcp_tool_discovery+code_evals"
    if mcp.command_configured or mcp.attempted:
        return "phoenix_mcp_attempt+code_evals"
    return "code_evals"


def _mcp_signal(mcp: PhoenixMcpResult) -> str:
    if mcp.status == "ok" and mcp.queried_tool_names:
        counts = ", ".join(f"{name}={mcp.resource_counts.get(name, 0)}" for name in mcp.queried_tool_names)
        return f"mcp:{mcp.status}:read_queries:{counts}"
    if mcp.status == "discovery_only":
        return f"mcp:{mcp.status}:tools={len(mcp.tool_names)}"
    return f"mcp:{mcp.status}:attempted={str(mcp.attempted).lower()}"


def _receipts(
    mcp: PhoenixMcpResult,
    evals: tuple[EvalResult, ...],
    findings: list[Finding],
    evidence: list[EvidenceItem],
) -> tuple[str, ...]:
    receipts: list[str] = [
        f"run:evidence_items={len(evidence)}",
        f"run:findings={len(findings)}",
        f"mcp:status={mcp.status}",
    ]
    receipts.extend(f"mcp:query:{name}={mcp.resource_counts.get(name, 0)}" for name in mcp.queried_tool_names)
    receipts.extend(f"eval:{item.name}={item.score:.2f}:{item.status}" for item in evals)
    return tuple(receipts)
