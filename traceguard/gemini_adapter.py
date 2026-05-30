from __future__ import annotations

import json
import os
from dataclasses import asdict

from .config import RuntimeConfig
from .models import EvalResult, EvidenceItem, Finding


def synthesize_incident_brief(
    findings: list[Finding],
    evidence: list[EvidenceItem],
    evals: list[EvalResult],
    mode: str,
) -> dict[str, str | bool]:
    config = RuntimeConfig.from_env()
    status = gemini_runtime_status(config)
    if not config.enable_gemini_synthesis:
        return {
            **status,
            "ok": False,
            "enabled": False,
            "text": "",
            "detail": "Gemini synthesis disabled; deterministic findings still produced.",
        }
    if not config.google_cloud_project:
        return {
            **status,
            "ok": False,
            "enabled": True,
            "text": "",
            "detail": "GOOGLE_CLOUD_PROJECT is required for Vertex AI Gemini production mode.",
        }
    try:
        text = _call_google_genai(config, _build_prompt(findings, evidence, evals, mode))
        return {
            **status,
            "ok": True,
            "enabled": True,
            "text": text,
            "detail": "Gemini synthesized the incident commander brief from deterministic findings only.",
        }
    except ImportError as exc:
        return {
            **status,
            "ok": False,
            "enabled": True,
            "text": "",
            "detail": f"Production dependency missing: {exc.name or 'google-genai'}",
        }
    except Exception as exc:  # pragma: no cover - requires live Google Cloud credentials
        return {
            **status,
            "ok": False,
            "enabled": True,
            "text": "",
            "detail": f"Gemini synthesis failed: {_safe_error(exc)}",
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
        "Immediate Actions, Detection Improvements, and Human Review Notes. Use only this JSON:\n\n"
        f"{json.dumps(payload, sort_keys=True)}"
    )


def _safe_error(exc: Exception) -> str:
    api_key = os.getenv("PHOENIX_API_KEY", "")
    message = str(exc)
    if api_key:
        message = message.replace(api_key, "[redacted]")
    return message[:500]
