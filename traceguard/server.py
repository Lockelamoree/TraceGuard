from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

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
PUBLIC_GET_PATHS = {
    "/",
    "/index.html",
    "/styles.css",
    "/app.js",
    "/favicon.ico",
    "/health",
    "/healthz",
    "/api/auth/status",
}
PUBLIC_POST_PATHS = {"/api/auth/login", "/api/auth/logout"}
LOGIN_FAILURE_LIMIT = 8
LOGIN_WINDOW_SECONDS = 5 * 60
LOGIN_LOCKOUT_SECONDS = 5 * 60
_LOGIN_LOCK = threading.Lock()
_LOGIN_FAILURES: dict[str, tuple[int, float, float]] = {}


class TraceGuardHandler(BaseHTTPRequestHandler):
    server_version = "TraceGuard/0.1"

    def do_GET(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        if path in {"/health", "/healthz"}:
            self._send(200, b"ok", "text/plain")
            return
        if path == "/api/auth/status":
            self._send_auth_status()
            return
        if self._requires_auth(path, "GET") and not self._authenticated():
            self._send_json(401, {"error": "authentication required", "authenticated": False})
            return
        if path == "/api/runtime":
            self._send_json(200, RuntimeConfig.from_env().public_status())
            return
        if path == "/sample":
            self._send_file(ROOT / "samples" / "gcp_incident_bundle.txt")
            return
        if path == "/":
            self._send_file(WEB_ROOT / "index.html")
            return
        candidate = (WEB_ROOT / path.lstrip("/")).resolve()
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT.resolve():
            self._send(403, b"forbidden", "text/plain")
            return
        self._send_file(candidate)

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
            self._send(200, analyze_json(body), "application/json")
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self._send(400, json.dumps({"error": str(exc)}).encode("utf-8"), "application/json")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[traceguard] {self.address_string()} {fmt % args}")

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), content_type)

    def _handle_login(self) -> None:
        config = auth_config_from_env()
        if not config.enabled:
            self._send_json(200, {"enabled": False, "authenticated": True})
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

    def _send_auth_status(self) -> None:
        config = auth_config_from_env()
        self._send_json(
            200,
            {"enabled": config.enabled, "authenticated": self._authenticated(config)},
        )

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
        forwarded_for = self.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        return forwarded_for or self.client_address[0]

    def _send_json(self, status: int, payload: dict, headers: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json", headers=headers)

    def _send(self, status: int, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> None:
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
