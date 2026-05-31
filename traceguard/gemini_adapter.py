from __future__ import annotations

import json
import os
import re
from dataclasses import asdict

from .config import RuntimeConfig
from .models import EvalResult, EvidenceItem, Finding


def synthesize_incident_brief(
    findings: list[Finding],
    evidence: list[EvidenceItem],
    evals: list[EvalResult],
    mode: str,
) -> dict[str, object]:
    config = RuntimeConfig.from_env()
    status = gemini_runtime_status(config)
    if not config.enable_gemini_synthesis:
        return {
            **status,
            "ok": False,
            "enabled": False,
            "text": "",
            "detail": "Gemini synthesis disabled; deterministic findings still produced.",
            "validation_status": "not_run",
            "accepted_claims": 0,
            "rejected_claims": 0,
        }
    if not config.google_cloud_project:
        return {
            **status,
            "ok": False,
            "enabled": True,
            "text": "",
            "detail": "GOOGLE_CLOUD_PROJECT is required for Vertex AI Gemini production mode.",
            "validation_status": "not_run",
            "accepted_claims": 0,
            "rejected_claims": 0,
        }
    try:
        text = _call_google_genai(config, _build_prompt(findings, evidence, evals, mode))
        validation = _validate_gemini_brief(text, findings, evidence)
        if not validation["ok"]:
            return {
                **status,
                "ok": False,
                "enabled": True,
                "text": "",
                "detail": f"Gemini brief rejected by evidence validator: {validation['validation_detail']}",
                **validation,
            }
        return {
            **status,
            "ok": True,
            "enabled": True,
            "text": text,
            "detail": (
                "Gemini synthesized the incident commander brief from deterministic findings only; "
                f"validator accepted {validation['accepted_claims']} evidence references and rejected "
                f"{validation['rejected_claims']} unsupported references."
            ),
            **validation,
        }
    except ImportError as exc:
        return {
            **status,
            "ok": False,
            "enabled": True,
            "text": "",
            "detail": f"Production dependency missing: {exc.name or 'google-genai'}",
            "validation_status": "not_run",
            "accepted_claims": 0,
            "rejected_claims": 0,
        }
    except Exception as exc:  # pragma: no cover - requires live Google Cloud credentials
        return {
            **status,
            "ok": False,
            "enabled": True,
            "text": "",
            "detail": f"Gemini synthesis failed: {_safe_error(exc)}",
            "validation_status": "not_run",
            "accepted_claims": 0,
            "rejected_claims": 0,
        }


def gemini_runtime_status(config: RuntimeConfig | None = None) -> dict[str, str | bool]:
    config = config or RuntimeConfig.from_env()
    return {
        "provider": "Google Cloud Gemini on Vertex AI",
        "configured": bool(config.google_cloud_project),
        "project": config.google_cloud_project,
        "location": config.google_cloud_location,
        "use_vertexai": config.google_genai_use_vertexai,
        "model": config.gemini_model,
    }


def _call_google_genai(config: RuntimeConfig, prompt: str) -> str:
    if config.google_genai_use_vertexai:
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
    from google import genai
    from google.genai.types import HttpOptions

    client = genai.Client(
        vertexai=config.google_genai_use_vertexai,
        project=config.google_cloud_project,
        location=config.google_cloud_location,
        http_options=HttpOptions(api_version="v1"),
    )
    response = client.models.generate_content(
        model=config.gemini_model,
        contents=prompt,
    )
    text = getattr(response, "text", "") or ""
    if not text.strip():
        raise RuntimeError("Gemini returned an empty response")
    return text.strip()


def _build_prompt(
    findings: list[Finding],
    evidence: list[EvidenceItem],
    evals: list[EvalResult],
    mode: str,
) -> str:
    payload = {
        "mode": mode,
        "guardrails": [
            "Do not create new findings.",
            "Only summarize and prioritize findings already present in deterministic_findings.",
            "Cite evidence IDs next to every confirmed security claim.",
            "If evidence is insufficient, say so plainly.",
            "Keep confirmed facts separate from recommended next steps.",
        ],
        "deterministic_findings": [asdict(finding) for finding in findings],
        "evidence_index": [
            {
                "id": item.id,
                "kind": item.kind,
                "title": item.title,
                "detail": item.detail[:700],
            }
            for item in evidence
        ],
        "quality_evals": [asdict(item) for item in evals],
    }
    return (
        "You are TraceGuard, a Google Cloud incident triage agent. Produce a concise markdown "
        "incident commander brief with these sections: Priority, Confirmed Evidence, Attack Path, "
        "Immediate Actions, Detection Improvements, and Human Review Notes. Every confirmed claim must cite "
        "one or more evidence IDs from the evidence_index. Use only this JSON:\n\n"
        f"{json.dumps(payload, sort_keys=True)}"
    )


def _validate_gemini_brief(
    text: str,
    findings: list[Finding],
    evidence: list[EvidenceItem],
) -> dict[str, object]:
    evidence_ids = {item.id for item in evidence}
    required_ids = {evidence_id for finding in findings for evidence_id in finding.evidence_ids}
    evidence_like_ids = set(
        re.findall(r"\b(?:audit|iam|repo|repo-line|tf|alert|json|raw)-[A-Za-z0-9_-]+\b", text)
    )
    unsupported_ids = sorted(evidence_like_ids - evidence_ids)
    accepted_ids = sorted(evidence_like_ids & evidence_ids)
    if unsupported_ids:
        return {
            "ok": False,
            "validation_status": "fail",
            "accepted_claims": len(accepted_ids),
            "rejected_claims": len(unsupported_ids),
            "unsupported_evidence_ids": unsupported_ids,
            "validation_detail": f"unsupported evidence IDs: {', '.join(unsupported_ids[:5])}",
        }
    if findings and not (set(accepted_ids) & required_ids):
        return {
            "ok": False,
            "validation_status": "fail",
            "accepted_claims": len(accepted_ids),
            "rejected_claims": 1,
            "unsupported_evidence_ids": [],
            "validation_detail": "brief did not cite any evidence IDs tied to deterministic findings",
        }
    return {
        "ok": True,
        "validation_status": "pass",
        "accepted_claims": len(accepted_ids),
        "rejected_claims": 0,
        "unsupported_evidence_ids": [],
        "validation_detail": "brief cited only known evidence IDs",
    }


def _safe_error(exc: Exception) -> str:
    api_key = os.getenv("PHOENIX_API_KEY", "")
    message = str(exc)
    if api_key:
        message = message.replace(api_key, "[redacted]")
    return message[:500]
