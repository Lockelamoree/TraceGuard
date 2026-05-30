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
            },
        )
        self.env_patch.start()
        with server_module._LOGIN_LOCK:
            server_module._LOGIN_FAILURES.clear()
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

    def test_protected_routes_require_signed_session(self) -> None:
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/api/auth/status")[0], 200)
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

    def test_failed_login_attempts_are_throttled(self) -> None:
        for _ in range(server_module.LOGIN_FAILURE_LIMIT - 1):
            self.assertEqual(self.request("/api/auth/login", method="POST", body={"token": "wrong"})[0], 401)

        status, headers, _ = self.request("/api/auth/login", method="POST", body={"token": "wrong"})
        self.assertEqual(status, 429)
        self.assertIn("Retry-After", headers)

        status, _, _ = self.request("/api/auth/login", method="POST", body={"token": "local-test-token"})
        self.assertEqual(status, 200)

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
