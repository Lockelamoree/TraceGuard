from __future__ import annotations

from collections import Counter

from .models import EvalResult, EvidenceItem, Finding


def run_evals(findings: list[Finding], evidence: list[EvidenceItem]) -> list[EvalResult]:
    evidence_ids = {item.id for item in evidence}
    expected = _expected_finding_ids(evidence)
    if not findings:
        if evidence and not expected:
            return [
                EvalResult("risk_signal_coverage", 1.0, "pass", "No supported high-risk signals were present in the evidence bundle."),
                EvalResult("evidence_grounding", 1.0, "pass", "No unsupported findings were emitted."),
                EvalResult("false_positive_control", 1.0, "pass", "No findings were emitted for low-signal evidence."),
            ]
        return [
            EvalResult("risk_signal_coverage", 0.0, "fail", "No findings were produced from the evidence bundle."),
            EvalResult("evidence_grounding", 1.0, "pass", "No unsupported findings were emitted."),
        ]
    grounded = [finding for finding in findings if set(finding.evidence_ids).issubset(evidence_ids) and finding.evidence_ids]
    confirmed = [finding for finding in findings if finding.status == "confirmed"]
    detections = [finding for finding in findings if finding.detection.strip()]
    remediations = [finding for finding in findings if finding.remediation.strip()]
    actual_ids = {finding.id for finding in findings}
    covered_expected = actual_ids.intersection(expected)
    duplicate_score = _duplicate_score(findings)
    severity_score = _severity_calibration_score(findings, evidence)
    return [
        _result(
            "risk_signal_coverage",
            len(covered_expected) / len(expected) if expected else 1.0,
            "Expected supported risk signals are represented by finding IDs.",
        ),
        _result("evidence_grounding", len(grounded) / len(findings), "Findings cite known evidence IDs."),
        _result("confirmed_claim_hygiene", len(confirmed) / len(findings), "Findings distinguish confirmed issues from hypotheses."),
        _result("detection_usefulness", len(detections) / len(findings), "Findings include detection opportunities."),
        _result("remediation_usefulness", len(remediations) / len(findings), "Findings include actionable remediation."),
        _result("severity_calibration", severity_score, "Critical public access is ranked as critical in improved triage."),
        _result("duplicate_pressure", duplicate_score, "Findings avoid excessive repeated detector IDs across the same incident."),
    ]


def _result(name: str, score: float, detail: str) -> EvalResult:
    status = "pass" if score >= 0.8 else "warn" if score >= 0.5 else "fail"
    return EvalResult(name, round(score, 2), status, detail)


def _expected_finding_ids(evidence: list[EvidenceItem]) -> set[str]:
    expected: set[str] = set()
    for item in evidence:
        detail = item.detail.lower()
        if item.kind == "iam_policy" and ("roles/owner" in detail or "roles/editor" in detail):
            expected.add("iam-primitive-admin")
        if item.kind in {"iam_policy", "terraform"} and ("allusers" in detail or "allauthenticatedusers" in detail):
            expected.add("public-cloud-resource")
        if item.kind == "gcp_audit_log" and any(token in detail for token in ("setiampolicy", "generateaccesstoken", "serviceaccountkeys.create")):
            expected.add("suspicious-identity-change")
        if item.kind == "terraform" and ("0.0.0.0/0" in detail or "ingress" in detail):
            expected.add("broad-network-exposure")
        if item.kind == "alert" and any(token in detail for token in ("exfil", "credential", "token")):
            expected.add("credential-exfil-signal")
        if item.kind == "repo_metadata" and "disabled" in detail:
            expected.add("repo-control-gap")
    return expected


def _duplicate_score(findings: list[Finding]) -> float:
    if not findings:
        return 1.0
    counts = Counter(finding.id for finding in findings)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    return max(0.0, 1.0 - (repeated / len(findings)))


def _severity_calibration_score(findings: list[Finding], evidence: list[EvidenceItem]) -> float:
    public_signals = [
        item
        for item in evidence
        if item.kind in {"iam_policy", "terraform"}
        and ("allusers" in item.detail.lower() or "allauthenticatedusers" in item.detail.lower())
    ]
    if not public_signals:
        return 1.0
    critical_public_findings = [
        finding
        for finding in findings
        if finding.id == "public-cloud-resource" and finding.severity == "critical"
    ]
    return min(1.0, len(critical_public_findings) / len(public_signals))
