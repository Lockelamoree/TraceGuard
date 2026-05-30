from __future__ import annotations

import json
import re
from typing import Iterable

from .models import EvidenceItem


def parse_evidence_bundle(raw: str) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    items.extend(_parse_json_objects(raw))
    items.extend(_parse_terraform(raw))
    items.extend(_parse_alert_lines(raw))
    items.extend(_parse_repo_metadata(raw))
    if not items and raw.strip():
        items.append(
            EvidenceItem(
                id="raw-001",
                kind="raw_text",
                title="Unclassified evidence text",
                detail=raw[:1200],
                source="paste",
            )
        )
    return _dedupe(items)


def _parse_json_objects(raw: str) -> Iterable[EvidenceItem]:
    decoder = json.JSONDecoder()
    index = 0
    count = 0
    while index < len(raw):
        start = raw.find("{", index)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(raw[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        count += 1
        yield _json_to_evidence(count, obj)
        index = start + end


def _json_to_evidence(count: int, obj: object) -> EvidenceItem:
    if isinstance(obj, dict) and "protoPayload" in obj:
        payload = obj.get("protoPayload", {})
        method = payload.get("methodName", "unknown method")
        principal = payload.get("authenticationInfo", {}).get("principalEmail", "unknown principal")
        resource = payload.get("resourceName", obj.get("resource", {}).get("labels", {}))
        return EvidenceItem(
            id=f"audit-{count:03d}",
            kind="gcp_audit_log",
            title=f"GCP audit event: {method}",
            detail=json.dumps({"method": method, "principal": principal, "resource": resource}, sort_keys=True),
            source="json",
        )
    if isinstance(obj, dict) and ("bindings" in obj or "iamPolicy" in obj):
        policy = obj.get("iamPolicy", obj)
        return EvidenceItem(
            id=f"iam-{count:03d}",
            kind="iam_policy",
            title="GCP IAM policy",
            detail=json.dumps(policy, sort_keys=True),
            source="json",
        )
    if isinstance(obj, dict) and ("branch_protection" in obj or "secret_scanning" in obj):
        return EvidenceItem(
            id=f"repo-{count:03d}",
            kind="repo_metadata",
            title="Repository security metadata",
            detail=json.dumps(obj, sort_keys=True),
            source="json",
        )
    return EvidenceItem(
        id=f"json-{count:03d}",
        kind="json",
        title="JSON evidence object",
        detail=json.dumps(obj, sort_keys=True)[:1600],
        source="json",
    )


def _parse_terraform(raw: str) -> Iterable[EvidenceItem]:
    patterns = {
        "public_iam_member": r'resource\s+"google_[^"]+_iam_(?:member|binding)"[\s\S]{0,500}member\s*=\s*"allUsers"',
        "public_member_assignment": r'member\s*=\s*"allUsers"|member\s*=\s*"allAuthenticatedUsers"',
        "public_ingress": r'(ingress\s*=\s*"all"|allow_unauthenticated\s*=\s*true|0\.0\.0\.0/0)',
        "bucket_public": r'resource\s+"google_storage_bucket_iam_[^"]+"[\s\S]{0,500}allUsers',
    }
    for name, pattern in patterns.items():
        for idx, match in enumerate(re.finditer(pattern, raw, re.IGNORECASE), start=1):
            yield EvidenceItem(
                id=f"tf-{name}-{idx:03d}",
                kind="terraform",
                title=f"Terraform signal: {name.replace('_', ' ')}",
                detail=_clean(match.group(0)),
                source="terraform",
            )


def _parse_alert_lines(raw: str) -> Iterable[EvidenceItem]:
    keywords = ("alert", "exfil", "credential", "suspicious", "impossible travel", "severity", "token")
    for idx, line in enumerate(raw.splitlines(), start=1):
        lowered = line.lower()
        if any(keyword in lowered for keyword in keywords):
            yield EvidenceItem(
                id=f"alert-{idx:03d}",
                kind="alert",
                title="Security alert text",
                detail=line.strip()[:1000],
                source="alert",
            )


def _parse_repo_metadata(raw: str) -> Iterable[EvidenceItem]:
    for idx, line in enumerate(raw.splitlines(), start=1):
        lowered = line.lower()
        if "branch protection: disabled" in lowered or "secret scanning: disabled" in lowered:
            yield EvidenceItem(
                id=f"repo-line-{idx:03d}",
                kind="repo_metadata",
                title="Repository control weakness",
                detail=line.strip(),
                source="repo",
            )


def _dedupe(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[tuple[str, str]] = set()
    output: list[EvidenceItem] = []
    for item in items:
        key = (item.kind, item.detail)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:1000]
