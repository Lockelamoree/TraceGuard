from __future__ import annotations

import os
from dataclasses import asdict

from .agent import derive_findings, summarize
from .evals import run_evals
from .parsers import parse_evidence_bundle
from .scoring import severity_score


def triage_evidence_tool(evidence_text: str, mode: str = "improved") -> dict:
    """Deterministically triage cloud evidence for the ADK Gemini agent."""
    improved = mode != "baseline"
    evidence = parse_evidence_bundle(evidence_text)
    findings = derive_findings(evidence, improved=improved)
    evals = run_evals(findings, evidence)
    return {
        "summary": summarize(findings),
        "evidence_count": len(evidence),
        "findings": [
            {
                **asdict(finding),
                "score": severity_score(finding.severity, finding.confidence),
            }
            for finding in findings
        ],
        "evals": [asdict(item) for item in evals],
    }


def build_root_agent():
    try:
        from google.adk.agents.llm_agent import Agent
    except ImportError:
        return None

    return Agent(
        model=os.getenv("GEMINI_MODEL", "gemini-3-flash-preview"),
        name="traceguard",
        description="Evidence-grounded Google Cloud security incident triage agent.",
        instruction=(
            "You are TraceGuard. Use triage_evidence_tool before making security claims. "
            "Do not invent findings. Separate confirmed evidence from hypotheses. "
            "Return concise incident commander guidance with impact, remediation, detection, "
            "and human approval notes."
        ),
        tools=[triage_evidence_tool],
    )


root_agent = build_root_agent()
