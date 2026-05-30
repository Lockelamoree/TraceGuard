from __future__ import annotations

SEVERITY_RANK = {"info": 1, "low": 2, "medium": 3, "high": 4, "critical": 5}


def severity_score(severity: str, confidence: float) -> int:
    rank = SEVERITY_RANK.get(severity.lower(), 1)
    return round(rank * max(0.0, min(confidence, 1.0)) * 20)

