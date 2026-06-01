import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import traceguard.server as server_module
from traceguard.server import TraceGuardHandler


class TraceGuardAuthServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(
            os.environ,
            {
                "TRACEGUARD_AUTH_TOKEN": "local-test-token",
                "TRACEGUARD_AUTH_SESSION_SECONDS": "600",
                "GOOGLE_CLOUD_PROJECT": "",
                "GOOGLE_CLOUD_LOCATION": "",
                "ENABLE_GEMINI_SYNTHESIS": "",
                "PHOENIX_API_KEY": "",
                "PHOENIX_COLLECTOR_ENDPOINT": "",
                "TRACEGUARD_REQUIRE_AUTH": "true",
            },
        )
        self.env_patch.start()
        with server_module._LOGIN_LOCK:
            server_module._LOGIN_FAILURES.clear()
        with server_module._LATEST_RUN_LOCK:
            server_module._LATEST_RUN_RECEIPT = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), TraceGuardHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.env_patch.stop()
        with server_module._LOGIN_LOCK:
            server_module._LOGIN_FAILURES.clear()
        with server_module._LATEST_RUN_LOCK:
            server_module._LATEST_RUN_RECEIPT = None

    def test_protected_routes_require_signed_session(self) -> None:
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/", method="HEAD")[0], 200)
        self.assertEqual(self.request("/health")[0], 200)
        self.assertEqual(self.request("/healthz")[0], 200)
        self.assertEqual(self.request("/api/auth/status")[0], 200)
        self.assertEqual(self.request("/api/samples")[0], 200)
        self.assertEqual(self.request("/proof")[0], 200)
        self.assertEqual(self.request("/api/runtime")[0], 401)
        self.assertEqual(self.request("/sample")[0], 401)
        self.assertEqual(self.request("/api/analyze", method="POST", body={"evidence_text": "{}"})[0], 401)

        status, headers, _ = self.request(
            "/api/auth/login",
            method="POST",
            body={"token": "local-test-token"},
            headers={"x-forwarded-proto": "https"},
        )
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=Lax", cookie)

        session_cookie = cookie.split(";", 1)[0]
        self.assertEqual(self.request("/api/runtime", headers={"Cookie": session_cookie})[0], 200)
        self.assertEqual(self.request("/api/runtime", headers={"Cookie": session_cookie + "x"})[0], 401)

    def test_sample_manifest_and_bundle_selection(self) -> None:
        status, _, manifest_body = self.request("/api/samples")
        self.assertEqual(status, 200)
        manifest = json.loads(manifest_body)
        sample_ids = {item["id"] for item in manifest["samples"]}
        self.assertIn("incident-response", sample_ids)
        self.assertIn("storage-exfiltration", sample_ids)
        self.assertIn("low-signal-control", sample_ids)

        status, headers, _ = self.request("/api/auth/login", method="POST", body={"token": "local-test-token"})
        self.assertEqual(status, 200)
        session_cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, body = self.request("/sample?bundle=storage-exfiltration", headers={"Cookie": session_cookie})
        self.assertEqual(status, 200)
        self.assertIn("storage", body.lower())
        self.assertEqual(self.request("/sample?bundle=unknown", headers={"Cookie": session_cookie})[0], 404)

    def test_public_proof_endpoint_exposes_only_safe_receipts(self) -> None:
        status, _, body = self.request("/proof")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["project"], "TraceGuard")
        self.assertEqual(payload["security_boundary"]["auth_enabled"], True)
        self.assertEqual(payload["security_boundary"]["secrets_exposed"], False)
        self.assertIn("traceguard/adk_agent.py", payload["google_cloud"]["adk_root_agent"])
        self.assertTrue(payload["latest_run"]["available"])
        self.assertEqual(payload["latest_run"]["gemini_rejected_claims"], 0)
        self.assertIn("phoenix_mcp_status", payload["latest_run"])
        self.assertIn("Phoenix", payload["claim_boundary"])
        lowered = body.lower()
        self.assertNotIn("local-test-token", lowered)
        self.assertNotIn("phoenix_api_key", lowered)
        self.assertNotIn("traceguard_auth_token", lowered)
        self.assertEqual(self.request("/proof", method="HEAD")[0], 200)

    def test_failed_login_attempts_are_throttled(self) -> None:
        for _ in range(server_module.LOGIN_FAILURE_LIMIT - 1):
            self.assertEqual(self.request("/api/auth/login", method="POST", body={"token": "wrong"})[0], 401)

        status, headers, _ = self.request("/api/auth/login", method="POST", body={"token": "wrong"})
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)

        status, _, _ = self.request("/api/auth/login", method="POST", body={"token": "local-test-token"})
        self.assertEqual(status, 200)

    def test_login_throttling_ignores_spoofed_forwarded_for(self) -> None:
        for index in range(server_module.LOGIN_FAILURE_LIMIT - 1):
            status, _, _ = self.request(
                "/api/auth/login",
                method="POST",
                body={"token": "wrong"},
                headers={"X-Forwarded-For": f"203.0.113.{index}"},
            )
            self.assertEqual(status, 401)

        status, headers, _ = self.request(
            "/api/auth/login",
            method="POST",
            body={"token": "wrong"},
            headers={"X-Forwarded-For": "203.0.113.250"},
        )
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)

    def test_required_auth_fails_closed_when_token_is_missing(self) -> None:
        with patch.dict(os.environ, {"TRACEGUARD_AUTH_TOKEN": "", "TRACEGUARD_REQUIRE_AUTH": "true"}):
            status, _, body = self.request("/api/auth/status")
            self.assertEqual(status, 200)
            self.assertIn('"enabled": true', body)
            self.assertIn('"authenticated": false', body)
            self.assertEqual(self.request("/api/runtime")[0], 401)
            login_status, _, login_body = self.request("/api/auth/login", method="POST", body={"token": "anything"})
            self.assertEqual(login_status, 503)
            self.assertIn("no access key is configured", login_body)

    def test_routes_are_public_when_auth_is_not_configured(self) -> None:
        with patch.dict(os.environ, {"TRACEGUARD_AUTH_TOKEN": "", "TRACEGUARD_REQUIRE_AUTH": ""}):
            status, _, body = self.request("/api/auth/status")
            self.assertEqual(status, 200)
            self.assertIn('"enabled": false', body)
            self.assertIn('"authenticated": true', body)
            self.assertEqual(self.request("/api/runtime")[0], 200)
            self.assertEqual(self.request("/sample?bundle=low-signal-control")[0], 200)
            self.assertEqual(self.request("/api/analyze", method="POST", body={"evidence_text": "{}"})[0], 200)
            status, _, proof_body = self.request("/proof")
            self.assertEqual(status, 200)
            payload = json.loads(proof_body)
            self.assertEqual(payload["security_boundary"]["auth_enabled"], False)
            self.assertEqual(payload["security_boundary"]["protected_routes"], [])

    def test_authenticated_analyze_rejects_cross_origin_posts(self) -> None:
        status, headers, _ = self.request("/api/auth/login", method="POST", body={"token": "local-test-token"})
        self.assertEqual(status, 200)
        session_cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(
            self.request(
                "/api/analyze",
                method="POST",
                body={"evidence_text": "{}", "mode": "improved"},
                headers={"Cookie": session_cookie, "Origin": "https://evil.example"},
            )[0],
            403,
        )
        self.assertEqual(
            self.request(
                "/api/analyze",
                method="POST",
                body={"evidence_text": "{}", "mode": "improved"},
                headers={"Cookie": session_cookie, "Origin": self.base_url},
            )[0],
            200,
        )
        status, _, body = self.request("/proof")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["latest_run"]["source"], "runtime_authenticated_run")
        self.assertEqual(payload["latest_run"]["mode"], "improved")
        self.assertIn("run_duration_ms", payload["latest_run"])

    def request(self, path: str, *, method: str = "GET", body=None, headers=None) -> tuple[int, dict[str, str], str]:
        data = None
        request_headers = headers or {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers = {"content-type": "application/json", **request_headers}
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8")


if __name__ == "__main__":
    unittest.main()
