from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .agent import analyze_json
from .auth import (
    auth_config_from_env,
    build_logout_cookie,
    build_session_cookie,
    issue_session,
    validate_session_cookie,
    verify_access_token,
)
from .config import RuntimeConfig

ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web"
LATEST_RUN_RECEIPT_PATH = ROOT / "docs" / "latest-run-receipt.json"
LATEST_RUN_CACHE_PATH = Path(os.getenv("TRACEGUARD_LATEST_RUN_PATH", "/tmp/traceguard-latest-run.json"))
SAMPLE_BUNDLES = {
    "incident-response": {
        "label": "Incident response bundle",
        "description": (
            "Public Cloud Run access, primitive IAM, suspicious token activity, broad ingress, "
            "and disabled repo controls."
        ),
        "file": "gcp_incident_bundle.txt",
    },
    "storage-exfiltration": {
        "label": "Storage exfiltration bundle",
        "description": "Cloud Storage exposure, suspicious data access, permissive IAM, and exfiltration-style alert text.",
        "file": "gcp_storage_exfil_bundle.txt",
    },
    "low-signal-control": {
        "label": "Low-signal control bundle",
        "description": "A quieter control sample for checking that TraceGuard stays inconclusive when evidence is weak.",
        "file": "gcp_low_signal_control_bundle.txt",
    },
}
DEFAULT_SAMPLE_BUNDLE = "incident-response"
PUBLIC_GET_PATHS = {
    "/",
    "/index.html",
    "/styles.css",
    "/app.js",
    "/favicon.ico",
    "/health",
    "/healthz",
    "/proof",
    "/api/auth/status",
    "/api/samples",
}
PUBLIC_POST_PATHS = {"/api/auth/login", "/api/auth/logout"}
LOGIN_FAILURE_LIMIT = 8
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_LOCKOUT_SECONDS = 5 * 60
_LOGIN_LOCK = threading.Lock()
_LOGIN_FAILURES: dict[str, tuple[int, float, float]] = {}
_LATEST_RUN_LOCK = threading.Lock()
_LATEST_RUN_RECEIPT: dict[str, object] | None = None


class TraceGuardHandler(BaseHTTPRequestHandler):
    server_version = "TraceGuard/0.1"

    def do_GET(self) -> None:
        self._handle_get(send_body=True)

    def do_HEAD(self) -> None:
        self._handle_get(send_body=False)

    def _handle_get(self, *, send_body: bool) -> None:
        parsed_url = urlparse(self.path)
        path = unquote(parsed_url.path)
        if path in {"/health", "/healthz"}:
            self._send(200, b"ok", "text/plain", send_body=send_body)
            return
        if path == "/api/auth/status":
            self._send_auth_status(send_body=send_body)
            return
        if path == "/proof":
            self._send_json(200, self._proof_payload(), send_body=send_body)
            return
        if self._requires_auth(path, "GET") and not self._authenticated():
            self._send_json(401, {"error": "authentication required", "authenticated": False}, send_body=send_body)
            return
        if path == "/api/runtime":
            self._send_json(200, RuntimeConfig.from_env().public_status(), send_body=send_body)
            return
        if path == "/api/samples":
            self._send_json(200, {"samples": _sample_manifest()}, send_body=send_body)
            return
        if path == "/sample":
            sample_path = _sample_path(_requested_sample_bundle(parsed_url.query))
            if sample_path is None:
                self._send_json(404, {"error": "sample bundle not found"}, send_body=send_body)
                return
            self._send_file(sample_path, send_body=send_body)
            return
        if path == "/":
            self._send_file(WEB_ROOT / "index.html", send_body=send_body)
            return
        candidate = (WEB_ROOT / path.lstrip("/")).resolve()
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
            self._send(403, b"forbidden", "text/plain", send_body=send_body)
            return
        self._send_file(candidate, send_body=send_body)

    def do_POST(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        if path == "/api/auth/login":
            self._handle_login()
            return
        if path == "/api/auth/logout":
            self._handle_logout()
            return
        if self._requires_auth(path, "POST") and not self._authenticated():
            self._send_json(401, {"error": "authentication required", "authenticated": False})
            return
        if path == "/api/analyze" and not self._same_origin_request():
            self._send_json(403, {"error": "cross-origin request blocked"})
            return
        if path != "/api/analyze":
            self._send(404, b"not found", "text/plain")
            return
        length = min(int(self.headers.get("content-length", "0")), 2_000_000)
        try:
            body = self.rfile.read(length)
            response_body = analyze_json(body)
            _record_latest_run_receipt(response_body)
            self._send(200, response_body, "application/json")
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self._send(400, json.dumps({"error": str(exc)}).encode("utf-8"), "application/json")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[traceguard] {self.address_string()} {fmt % args}")

    def _send_file(self, path: Path, *, send_body: bool = True) -> None:
        if not path.is_file():
            self._send(404, b"not found", "text/plain", send_body=send_body)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), content_type, send_body=send_body)

    def _handle_login(self) -> None:
        config = auth_config_from_env()
        if not config.enabled:
            self._send_json(200, {"enabled": False, "authenticated": True})
            return
        if not config.ready:
            self._send_json(
                503,
                {
                    "enabled": True,
                    "authenticated": False,
                    "error": "authentication is required but no access key is configured",
                },
            )
            return
        payload = self._read_json_body(max_bytes=16_384)
        candidate = payload.get("token") or payload.get("password") or ""
        client_key = self._client_key()
        if verify_access_token(candidate, config):
            _record_login_result(client_key, success=True)
        else:
            retry_after = _record_login_result(client_key, success=False)
            if retry_after > 0:
                self._send_json(
                    429,
                    {"enabled": True, "authenticated": False, "error": "too many invalid access key attempts"},
                    headers={"Retry-After": str(retry_after)},
                )
                return
            self._send_json(401, {"enabled": True, "authenticated": False, "error": "invalid access key"})
            return
        session_value = issue_session(config)
        self._send_json(
            200,
            {"enabled": True, "authenticated": True},
            headers={
                "Set-Cookie": build_session_cookie(
                    session_value,
                    secure=self._secure_request(),
                    max_age=config.session_seconds,
                )
            },
        )

    def _handle_logout(self) -> None:
        config = auth_config_from_env()
        self._send_json(
            200,
            {"enabled": config.enabled, "authenticated": False},
            headers={"Set-Cookie": build_logout_cookie(secure=self._secure_request())},
        )

    def _send_auth_status(self, *, send_body: bool = True) -> None:
        config = auth_config_from_env()
        self._send_json(
            200,
            {"enabled": config.enabled, "authenticated": self._authenticated(config)},
            send_body=send_body,
        )

    def _proof_payload(self) -> dict[str, object]:
        runtime = RuntimeConfig.from_env()
        auth = auth_config_from_env()
        return {
            "project": "TraceGuard",
            "repo": "https://github.com/Lockelamoree/TraceGuard",
            "hosted_app": "https://traceguard-cnhtsa5yrq-uc.a.run.app",
            "demo_path": ["Load sample", "Run agent", "Check Phoenix/eval receipts", "Copy cited report"],
            "google_cloud": {
                "cloud_run_runtime": True,
                "gemini_vertex_configured": runtime.enable_gemini_synthesis and runtime.google_genai_use_vertexai,
                "gemini_model": runtime.gemini_model,
                "adk_root_agent": "traceguard/adk_agent.py",
            },
            "arize": {
                "phoenix_project": runtime.phoenix_project_name,
                "phoenix_configured": runtime.phoenix_api_key_configured or bool(runtime.phoenix_collector_endpoint),
                "phoenix_mcp_configured": bool(runtime.phoenix_mcp_command),
                "phoenix_mcp_server": runtime.phoenix_mcp_server,
            },
            "deployment": {
                "cloud_run_service": os.getenv("K_SERVICE", ""),
                "cloud_run_revision": os.getenv("K_REVISION", ""),
                "source_commit": os.getenv("TRACEGUARD_SOURCE_COMMIT", ""),
            },
            "latest_run": _latest_run_receipt(),
            "security_boundary": {
                "auth_enabled": auth.enabled,
                "protected_routes": ["/sample", "/api/runtime", "/api/analyze"] if auth.enabled else [],
                "secrets_exposed": False,
            },
            "claim_boundary": (
                "TraceGuard reports live Gemini, Phoenix, and MCP only when the runtime result confirms those states. "
                "The current improvement loop is eval-guided and does not claim autonomous production self-modification."
            ),
        }

    def _read_json_body(self, *, max_bytes: int) -> dict[str, object]:
        length = min(int(self.headers.get("content-length", "0")), max_bytes)
        if length <= 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8-sig"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _requires_auth(self, path: str, method: str) -> bool:
        config = auth_config_from_env()
        if not config.enabled:
            return False
        if method == "GET" and path in PUBLIC_GET_PATHS:
            return False
        if method == "POST" and path in PUBLIC_POST_PATHS:
            return False
        return True

    def _authenticated(self, config=None) -> bool:
        auth_config = config or auth_config_from_env()
        return validate_session_cookie(self.headers.get("cookie"), auth_config)

    def _secure_request(self) -> bool:
        proto = self.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
        return proto == "https"

    def _same_origin_request(self) -> bool:
        host = self.headers.get("host", "")
        if not host:
            return False
        origin = self.headers.get("origin")
        if origin:
            return _url_host_matches(origin, host)
        referer = self.headers.get("referer")
        if referer:
            return _url_host_matches(referer, host)
        return True

    def _client_key(self) -> str:
        return self.client_address[0]

    def _send_json(
        self,
        status: int,
        payload: dict,
        headers: dict[str, str] | None = None,
        *,
        send_body: bool = True,
    ) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json", headers=headers, send_body=send_body)

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
        *,
        send_body: bool = True,
    ) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        self.send_header("x-frame-options", "DENY")
        if self._secure_request():
            self.send_header("strict-transport-security", "max-age=31536000; includeSubDomains")
        if content_type.startswith("text/html"):
            self.send_header(
                "content-security-policy",
                "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
            )
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if send_body:
            self.wfile.write(body)


def _record_login_result(client_key: str, *, success: bool, now: float | None = None) -> int:
    current_time = now if now is not None else time.monotonic()
    with _LOGIN_LOCK:
        if success:
            _LOGIN_FAILURES.pop(client_key, None)
            return 0

        count, first_seen, locked_until = _LOGIN_FAILURES.get(client_key, (0, current_time, 0.0))
        if locked_until > current_time:
            return max(1, int(locked_until - current_time))
        if current_time - first_seen > LOGIN_WINDOW_SECONDS:
            count, first_seen = 0, current_time
        count += 1
        if count >= LOGIN_FAILURE_LIMIT:
            locked_until = current_time + LOGIN_LOCKOUT_SECONDS
        else:
            locked_until = 0.0
        _LOGIN_FAILURES[client_key] = (count, first_seen, locked_until)
        return max(1, int(locked_until - current_time)) if locked_until else 0


def _url_host_matches(url: str, expected_host: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() == expected_host.lower()


def _record_latest_run_receipt(response_body: bytes) -> None:
    try:
        result = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(result, dict) or result.get("error"):
        return
    receipt = _sanitize_run_receipt(result)
    if not receipt:
        return
    with _LATEST_RUN_LOCK:
        global _LATEST_RUN_RECEIPT
        _LATEST_RUN_RECEIPT = receipt
    try:
        LATEST_RUN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LATEST_RUN_CACHE_PATH.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def _sample_manifest() -> list[dict[str, str]]:
    return [
        {
            "id": sample_id,
            "label": str(metadata["label"]),
            "description": str(metadata["description"]),
        }
        for sample_id, metadata in SAMPLE_BUNDLES.items()
    ]


def _requested_sample_bundle(query: str) -> str:
    values = parse_qs(query).get("bundle", [])
    candidate = values[0] if values else DEFAULT_SAMPLE_BUNDLE
    return candidate if candidate in SAMPLE_BUNDLES else ""


def _sample_path(bundle: str) -> Path | None:
    metadata = SAMPLE_BUNDLES.get(bundle)
    if not metadata:
        return None
    sample_path = (ROOT / "samples" / str(metadata["file"])).resolve()
    samples_root = (ROOT / "samples").resolve()
    if sample_path.parent != samples_root:
        return None
    return sample_path


def _latest_run_receipt() -> dict[str, object]:
    with _LATEST_RUN_LOCK:
        if _LATEST_RUN_RECEIPT:
            return dict(_LATEST_RUN_RECEIPT)
    for path in (LATEST_RUN_CACHE_PATH, LATEST_RUN_RECEIPT_PATH):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {
        "available": False,
        "detail": "No sanitized hosted run receipt has been recorded yet.",
    }


def _sanitize_run_receipt(result: dict[str, object]) -> dict[str, object]:
    metrics = _dict(result.get("metrics"))
    gemini = _dict(result.get("gemini"))
    arize = _dict(result.get("arize"))
    mcp = _dict(arize.get("mcp"))
    improvement = _dict(result.get("improvement"))
    evidence = _list(result.get("evidence"))
    findings = _list(result.get("findings"))
    queried_tools = [str(item) for item in _list(mcp.get("queried_tool_names"))]
    source = "runtime_authenticated_run" if auth_config_from_env().enabled else "runtime_public_run"
    return {
        "available": True,
        "source": source,
        "checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cloud_run_revision": os.getenv("K_REVISION", ""),
        "source_commit": os.getenv("TRACEGUARD_SOURCE_COMMIT", ""),
        "run_id": str(result.get("run_id", "")),
        "mode": str(result.get("mode", "")),
        "evidence_items": _int(metrics.get("evidence_count"), len(evidence)),
        "findings": _int(metrics.get("finding_count"), len(findings)),
        "critical_or_high": _int(metrics.get("critical_high_count"), 0),
        "unsupported_confirmed_claims": _int(metrics.get("unsupported_confirmed_claims"), 0),
        "eval_average": _float(metrics.get("eval_average"), 0.0),
        "run_duration_ms": _int(metrics.get("duration_ms"), 0),
        "gemini_provider": str(gemini.get("provider", "Google Cloud Gemini on Vertex AI")),
        "gemini_model": str(gemini.get("model", "")),
        "gemini_ok": bool(gemini.get("ok")),
        "gemini_validation_status": str(gemini.get("validation_status", "not_run")),
        "gemini_accepted_claims": _int(gemini.get("accepted_claims"), 0),
        "gemini_rejected_claims": _int(gemini.get("rejected_claims"), 0),
        "arize_tracing_ready": bool(arize.get("tracing_ready")),
        "arize_phoenix_enabled": bool(arize.get("phoenix_enabled")),
        "arize_project": str(arize.get("phoenix_project", "")),
        "phoenix_mcp_status": str(mcp.get("status", "")),
        "phoenix_mcp_tool_count": _int(mcp.get("tool_count"), len(_list(mcp.get("tool_names")))),
        "phoenix_mcp_queried_tools": queried_tools,
        "phoenix_mcp_queried_tool_count": _int(mcp.get("queried_tool_count"), len(queried_tools)),
        "phoenix_mcp_resource_counts": _dict(mcp.get("resource_counts")),
        "improvement_status": str(improvement.get("status", "")),
        "improvement_source": str(improvement.get("source", "")),
        "improvement_receipts": _int(metrics.get("improvement_receipt_count"), len(_list(improvement.get("receipts")))),
    }


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TraceGuard web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), TraceGuardHandler)
    print(f"TraceGuard listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
