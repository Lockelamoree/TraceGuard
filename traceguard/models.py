from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    kind: str
    title: str
    detail: str
    source: str


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    severity: str
    confidence: float
    status: str
    summary: str
    impact: str
    remediation: str
    detection: str
    cwe: str
    evidence_ids: tuple[str, ...]
    mitre_attack: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AgentStep:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class EvalResult:
    name: str
    score: float
    status: str
    detail: str

