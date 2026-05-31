import os
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from traceguard import observability
from traceguard.agent import analyze_bundle, analyze_json, derive_findings
from traceguard.adk_agent import triage_evidence_tool
from traceguard.auth import (
    COOKIE_NAME,
    AuthConfig,
    build_session_cookie,
    issue_session,
    validate_session_cookie,
    verify_access_token,
)
from traceguard.config import RuntimeConfig
from traceguard.observability import TraceContext
from traceguard.parsers import parse_evidence_bundle
from traceguard.phoenix_mcp import _mcp_environment, _safe_error, inspect_phoenix_mcp


class FakeSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, object] = {}
        self.events: list[tuple[str, dict[str, object]]] = []

    def __enter__(self) -> "FakeSpan":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.events.append((name, dict(attributes or {})))


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_as_current_span(self, name: str) -> FakeSpan:
        span = FakeSpan(name)
        self.spans.append(span)
        return span


class FakeMcpProcess:
    def __init__(self, stdout_bytes: bytes) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(stdout_bytes)
        self.stderr = io.BytesIO()
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def mcp_frame(message: dict[str, object]) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return payload + b"\n"


class TraceGuardAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(
            os.environ,
            {
                "GOOGLE_CLOUD_PROJECT": "",
                "GOOGLE_CLOUD_LOCATION": "",
                "GOOGLE_GENAI_USE_VERTEXAI": "",
                "ENABLE_GEMINI_SYNTHESIS": "",
                "PHOENIX_API_KEY": "",
                "PHOENIX_BASE_URL": "",
                "PHOENIX_COLLECTOR_ENDPOINT": "",
                "PHOENIX_CLIENT_HEADERS": "",
                "PHOENIX_MCP_COMMAND": "",
                "PHOENIX_MCP_TIMEOUT_SECONDS": "",
                "TRACEGUARD_AUTH_TOKEN": "",
                "TRACEGUARD_AUTH_SESSION_SECONDS": "",
            },
        )
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()

    def test_sample_bundle_produces_confirmed_findings(self) -> None:
        raw = Path("samples/gcp_incident_bundle.txt").read_text(encoding="utf-8")
        result = analyze_bundle(raw, "improved")
        self.assertTrue(result["findings"])
        self.assertTrue(any(finding["severity"] == "critical" for finding in result["findings"]))
        self.assertTrue(all(finding["status"] == "confirmed" for finding in result["findings"]))
        self.assertTrue(all(finding["evidence_ids"] for finding in result["findings"]))
        self.assertEqual(result["metrics"]["unsupported_confirmed_claims"], 0)
        self.assertGreater(result["metrics"]["duration_ms"], 0)
        self.assertGreaterEqual(result["metrics"]["eval_average"], 0.8)
        self.assertIn("gemini", result)
        self.assertFalse(result["gemini"]["enabled"])

    def test_all_sample_bundles_render_reports(self) -> None:
        for sample_path in Path("samples").glob("*.txt"):
            with self.subTest(sample=sample_path.name):
                result = analyze_bundle(sample_path.read_text(encoding="utf-8"), "improved")
                self.assertIn("report_markdown", result)
                self.assertGreater(len(result["report_markdown"]), 500)
                self.assertIn("Phoenix MCP introspection", [step["name"] for step in result["steps"]])

    def test_observability_spans_capture_triage_context(self) -> None:
        raw = Path("samples/gcp_incident_bundle.txt").read_text(encoding="utf-8")
        fake_tracer = FakeTracer()
        with patch.multiple(
            observability,
            _TRACING_ATTEMPTED=True,
            _TRACING_READY=True,
            _TRACING_ERROR="",
            _TRACER=fake_tracer,
        ), patch.dict(os.environ, {"PHOENIX_COLLECTOR_ENDPOINT": "http://localhost:6006/v1/traces"}):
            result = analyze_bundle(raw, "improved")

        spans = {span.name: span for span in fake_tracer.spans}
        for span_name in (
            "parse_evidence",
            "derive_findings",
            "run_evals",
            "gemini_synthesis",
            "phoenix_mcp_introspection",
            "render_report",
        ):
            self.assertIn(span_name, spans)
            self.assertEqual(spans[span_name].attributes["traceguard.run_mode"], "improved")

        parse_span = spans["parse_evidence"]
        self.assertEqual(parse_span.attributes["traceguard.evidence_count"], len(result["evidence"]))
        self.assertIn("iam_policy", parse_span.attributes["traceguard.evidence_kinds"])
        self.assertIn("traceguard.evidence.parsed", [name for name, _ in parse_span.events])

        finding_span = spans["derive_findings"]
        self.assertEqual(finding_span.attributes["traceguard.finding_count"], len(result["findings"]))
        self.assertEqual(finding_span.attributes["traceguard.finding_ids"], tuple(finding["id"] for finding in result["findings"]))
        self.assertIn("critical", finding_span.attributes["traceguard.finding_severities"])
        self.assertIn("traceguard.findings.derived", [name for name, _ in finding_span.events])

        eval_span = spans["run_evals"]
        self.assertEqual(eval_span.attributes["traceguard.eval_scores"], tuple(item["score"] for item in result["evals"]))
        self.assertEqual(eval_span.attributes["traceguard.eval_statuses"], tuple(item["status"] for item in result["evals"]))
        self.assertEqual(eval_span.attributes["traceguard.eval.evidence_grounding.status"], "pass")
        self.assertIn("traceguard.evals.completed", [name for name, _ in eval_span.events])

        gemini_span = spans["gemini_synthesis"]
        self.assertEqual(gemini_span.attributes["traceguard.gemini_status"], "disabled")
        self.assertFalse(gemini_span.attributes["traceguard.gemini_enabled"])
        self.assertFalse(gemini_span.attributes["traceguard.gemini_ok"])
        self.assertIn("disabled", str(gemini_span.attributes["traceguard.gemini_detail"]).lower())
        self.assertIn("traceguard.gemini.status", [name for name, _ in gemini_span.events])

        mcp_span = spans["phoenix_mcp_introspection"]
        self.assertEqual(mcp_span.attributes["traceguard.phoenix_mcp_status"], "command_not_configured")
        self.assertFalse(mcp_span.attributes["traceguard.phoenix_mcp_attempted"])
        self.assertFalse(mcp_span.attributes["traceguard.phoenix_mcp_command_configured"])
        self.assertIn("traceguard.phoenix_mcp.completed", [name for name, _ in mcp_span.events])

        report_span = spans["render_report"]
        self.assertEqual(report_span.attributes["traceguard.report_length"], len(result["report_markdown"]))
        self.assertIn("traceguard.report.rendered", [name for name, _ in report_span.events])

    def test_phoenix_client_headers_are_derived_for_older_cloud_spaces(self) -> None:
        with patch.dict(os.environ, {"PHOENIX_API_KEY": "test-key", "PHOENIX_CLIENT_HEADERS": ""}):
            observability._ensure_phoenix_client_headers()
            self.assertEqual(os.environ["PHOENIX_CLIENT_HEADERS"], "api_key=test-key")

        with patch.dict(os.environ, {"PHOENIX_API_KEY": "test-key", "PHOENIX_CLIENT_HEADERS": "x=y"}):
            observability._ensure_phoenix_client_headers()
            self.assertEqual(os.environ["PHOENIX_CLIENT_HEADERS"], "x=y")

    def test_phoenix_cloud_root_endpoint_is_not_marked_live(self) -> None:
        self.assertTrue(observability._phoenix_cloud_root_endpoint("https://app.phoenix.arize.com/"))
        self.assertFalse(observability._phoenix_cloud_root_endpoint("https://app.phoenix.arize.com/s/demo"))

    def test_local_phoenix_introspection_is_explicitly_demo_replay(self) -> None:
        result = analyze_bundle("{}", "improved")
        detail = next(step["detail"] for step in result["steps"] if step["name"] == "Phoenix MCP introspection")
        lowered = detail.lower()
        self.assertIn("simulated", lowered)
        self.assertIn("replay", lowered)
        self.assertIn("no live mcp trace query", lowered)
        self.assertNotIn("queried prior run traces", lowered)
        self.assertNotIn("can inspect the run traces", lowered)

    def test_phoenix_configured_without_mcp_command_is_explicit(self) -> None:
        raw = Path("samples/gcp_incident_bundle.txt").read_text(encoding="utf-8")
        fake_tracer = FakeTracer()
        with patch.multiple(
            observability,
            _TRACING_ATTEMPTED=True,
            _TRACING_READY=True,
            _TRACING_ERROR="",
            _TRACER=fake_tracer,
        ), patch.dict(os.environ, {"PHOENIX_COLLECTOR_ENDPOINT": "http://localhost:6006/v1/traces"}):
            result = analyze_bundle(raw, "improved")

        mcp = result["arize"]["mcp"]
        self.assertEqual(mcp["status"], "command_not_configured")
        self.assertFalse(mcp["attempted"])
        self.assertIn("PHOENIX_MCP_COMMAND", mcp["summary"])

    def test_phoenix_mcp_tool_discovery_uses_stdio_json_rpc(self) -> None:
        context = TraceContext(
            run_id="run-1",
            phoenix_project="traceguard-hackathon",
            phoenix_enabled=True,
            phoenix_collector_endpoint="https://app.phoenix.arize.com/s/demo",
            mcp_server="@arizeai/phoenix-mcp",
            tracing_ready=True,
            tracing_error="",
        )
        config = RuntimeConfig(
            google_cloud_project="traceguard-prod",
            google_cloud_location="us-central1",
            google_genai_use_vertexai=True,
            gemini_model="gemini-2.5-flash",
            enable_gemini_synthesis=True,
            phoenix_project_name="traceguard-hackathon",
            phoenix_base_url="https://app.phoenix.arize.com",
            phoenix_collector_endpoint="https://app.phoenix.arize.com/s/demo",
            phoenix_api_key_configured=True,
            phoenix_mcp_server="@arizeai/phoenix-mcp",
            phoenix_mcp_command="npx -y @arizeai/phoenix-mcp@4.0.13",
            phoenix_mcp_timeout_seconds=1,
            traceguard_auth_configured=True,
            traceguard_auth_required=True,
            traceguard_auth_session_seconds=3600,
        )
        stdout = (
            mcp_frame({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": [{"name": "list-traces"}, {"name": "list-projects"}]},
                }
            )
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"projects": [{"id": "project-1", "name": "traceguard-hackathon"}]}),
                            }
                        ]
                    },
                }
            )
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"traces": [{"trace_id": "trace-1"}, {"trace_id": "trace-2"}]}),
                            }
                        ]
                    },
                }
            )
        )
        fake_process = FakeMcpProcess(stdout)

        with patch("traceguard.phoenix_mcp.subprocess.Popen", return_value=fake_process) as popen:
            result = inspect_phoenix_mcp(context, improved=True, config=config)

        self.assertEqual(result.status, "ok")
        self.assertTrue(result.attempted)
        self.assertEqual(result.tool_names, ("list-projects", "list-traces"))
        self.assertEqual(result.queried_tool_names, ("list-projects", "list-traces"))
        self.assertEqual(result.resource_counts["list-projects"], 1)
        self.assertEqual(result.resource_counts["list-traces"], 2)
        sent = fake_process.stdin.getvalue().decode("utf-8")
        self.assertIn('"method":"initialize"', sent)
        self.assertIn('"method":"tools/list"', sent)
        self.assertIn('"method":"tools/call"', sent)
        self.assertIn('"name":"list-projects"', sent)
        self.assertIn('"name":"list-traces"', sent)
        popen.assert_called_once()

    def test_phoenix_mcp_environment_does_not_inherit_traceguard_secrets(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PHOENIX_API_KEY": "phoenix-secret",
                "PHOENIX_CLIENT_HEADERS": "api_key=phoenix-secret",
                "TRACEGUARD_AUTH_TOKEN": "traceguard-secret",
                "GOOGLE_APPLICATION_CREDENTIALS": "credential-path",
            },
            clear=True,
        ):
            config = RuntimeConfig.from_env()
            env = _mcp_environment(config)

        self.assertEqual(env["PHOENIX_API_KEY"], "phoenix-secret")
        self.assertEqual(env["PHOENIX_CLIENT_HEADERS"], "api_key=phoenix-secret")
        self.assertNotIn("TRACEGUARD_AUTH_TOKEN", env)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)

    def test_phoenix_mcp_error_redacts_configured_secrets(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PHOENIX_API_KEY": "phoenix-secret",
                "TRACEGUARD_AUTH_TOKEN": "traceguard-secret",
            },
        ):
            message = _safe_error(RuntimeError("failed with phoenix-secret and traceguard-secret"))

        self.assertNotIn("phoenix-secret", message)
        self.assertNotIn("traceguard-secret", message)
        self.assertEqual(message.count("[redacted]"), 2)

    def test_phoenix_mcp_authentication_html_is_not_counted_as_query_success(self) -> None:
        context = TraceContext(
            run_id="run-1",
            phoenix_project="traceguard-hackathon",
            phoenix_enabled=True,
            phoenix_collector_endpoint="https://app.phoenix.arize.com/s/demo",
            mcp_server="@arizeai/phoenix-mcp",
            tracing_ready=True,
            tracing_error="",
        )
        config = RuntimeConfig(
            google_cloud_project="traceguard-prod",
            google_cloud_location="us-central1",
            google_genai_use_vertexai=True,
            gemini_model="gemini-2.5-flash",
            enable_gemini_synthesis=True,
            phoenix_project_name="traceguard-hackathon",
            phoenix_base_url="https://app.phoenix.arize.com",
            phoenix_collector_endpoint="https://app.phoenix.arize.com/s/demo",
            phoenix_api_key_configured=True,
            phoenix_mcp_server="@arizeai/phoenix-mcp",
            phoenix_mcp_command="phoenix-mcp",
            phoenix_mcp_timeout_seconds=1,
            traceguard_auth_configured=True,
            traceguard_auth_required=True,
            traceguard_auth_session_seconds=3600,
        )
        stdout = (
            mcp_frame({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": [{"name": "list-projects"}, {"name": "list-traces"}]},
                }
            )
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "content": [
                            {"type": "text", "text": "<html><head><title>Authentication</title></head></html>"}
                        ]
                    },
                }
            )
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "result": {
                        "content": [
                            {"type": "text", "text": "<html><head><title>Authentication</title></head></html>"}
                        ]
                    },
                }
            )
        )

        with patch("traceguard.phoenix_mcp.subprocess.Popen", return_value=FakeMcpProcess(stdout)):
            result = inspect_phoenix_mcp(context, improved=True, config=config)

        self.assertEqual(result.status, "discovery_only")
        self.assertEqual(result.queried_tool_names, ())
        self.assertIn("authentication page", result.query_error)

    def test_phoenix_mcp_suppresses_optional_errors_after_successful_query(self) -> None:
        context = TraceContext(
            run_id="run-1",
            phoenix_project="traceguard-hackathon",
            phoenix_enabled=True,
            phoenix_collector_endpoint="https://app.phoenix.arize.com/s/demo",
            mcp_server="@arizeai/phoenix-mcp",
            tracing_ready=True,
            tracing_error="",
        )
        config = RuntimeConfig(
            google_cloud_project="traceguard-prod",
            google_cloud_location="us-central1",
            google_genai_use_vertexai=True,
            gemini_model="gemini-2.5-flash",
            enable_gemini_synthesis=True,
            phoenix_project_name="traceguard-hackathon",
            phoenix_base_url="https://app.phoenix.arize.com",
            phoenix_collector_endpoint="https://app.phoenix.arize.com/s/demo",
            phoenix_api_key_configured=True,
            phoenix_mcp_server="@arizeai/phoenix-mcp",
            phoenix_mcp_command="phoenix-mcp",
            phoenix_mcp_timeout_seconds=1,
            traceguard_auth_configured=True,
            traceguard_auth_required=True,
            traceguard_auth_session_seconds=3600,
        )
        stdout = (
            mcp_frame({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": [{"name": "list-projects"}, {"name": "list-traces"}]},
                }
            )
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "content": [
                            {"type": "text", "text": "<html><head><title>Authentication</title></head></html>"}
                        ]
                    },
                }
            )
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps({"traces": [{"trace_id": "trace-1"}]})}
                        ]
                    },
                }
            )
        )

        with patch("traceguard.phoenix_mcp.subprocess.Popen", return_value=FakeMcpProcess(stdout)):
            result = inspect_phoenix_mcp(context, improved=True, config=config)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.queried_tool_names, ("list-traces",))
        self.assertEqual(result.resource_counts["list-traces"], 1)
        self.assertEqual(result.query_error, "")

    def test_phoenix_mcp_can_run_when_otel_export_is_not_ready(self) -> None:
        context = TraceContext(
            run_id="run-1",
            phoenix_project="traceguard-hackathon",
            phoenix_enabled=True,
            phoenix_collector_endpoint="https://app.phoenix.arize.com",
            mcp_server="@arizeai/phoenix-mcp",
            tracing_ready=False,
            tracing_error="collector endpoint needs Phoenix Cloud space hostname",
        )
        config = RuntimeConfig(
            google_cloud_project="traceguard-prod",
            google_cloud_location="us-central1",
            google_genai_use_vertexai=True,
            gemini_model="gemini-2.5-flash",
            enable_gemini_synthesis=True,
            phoenix_project_name="traceguard-hackathon",
            phoenix_base_url="https://app.phoenix.arize.com",
            phoenix_collector_endpoint="https://app.phoenix.arize.com",
            phoenix_api_key_configured=True,
            phoenix_mcp_server="@arizeai/phoenix-mcp",
            phoenix_mcp_command="phoenix-mcp",
            phoenix_mcp_timeout_seconds=1,
            traceguard_auth_configured=True,
            traceguard_auth_required=True,
            traceguard_auth_session_seconds=3600,
        )
        stdout = (
            mcp_frame({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
            + mcp_frame({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "list-traces"}]}})
            + mcp_frame(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {"content": [{"type": "text", "text": json.dumps({"traces": []})}]},
                }
            )
        )

        with patch("traceguard.phoenix_mcp.subprocess.Popen", return_value=FakeMcpProcess(stdout)):
            result = inspect_phoenix_mcp(context, improved=True, config=config)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.queried_tool_names, ("list-traces",))
        self.assertIn("OTEL is not live", result.summary)

    def test_phoenix_mcp_rejects_unpinned_npx_package(self) -> None:
        context = TraceContext(
            run_id="run-1",
            phoenix_project="traceguard-hackathon",
            phoenix_enabled=True,
            phoenix_collector_endpoint="https://app.phoenix.arize.com/s/demo",
            mcp_server="@arizeai/phoenix-mcp",
            tracing_ready=True,
            tracing_error="",
        )
        config = RuntimeConfig(
            google_cloud_project="traceguard-prod",
            google_cloud_location="us-central1",
            google_genai_use_vertexai=True,
            gemini_model="gemini-2.5-flash",
            enable_gemini_synthesis=True,
            phoenix_project_name="traceguard-hackathon",
            phoenix_base_url="https://app.phoenix.arize.com",
            phoenix_collector_endpoint="https://app.phoenix.arize.com/s/demo",
            phoenix_api_key_configured=True,
            phoenix_mcp_server="@arizeai/phoenix-mcp",
            phoenix_mcp_command="npx -y @arizeai/phoenix-mcp@latest",
            phoenix_mcp_timeout_seconds=1,
            traceguard_auth_configured=True,
            traceguard_auth_required=True,
            traceguard_auth_session_seconds=3600,
        )

        result = inspect_phoenix_mcp(context, improved=True, config=config)
        self.assertEqual(result.status, "error")
        self.assertIn("exact version", result.error)

    def test_baseline_self_improvement_delta(self) -> None:
        raw = Path("samples/gcp_incident_bundle.txt").read_text(encoding="utf-8")
        baseline = analyze_bundle(raw, "baseline")
        improved = analyze_bundle(raw, "improved")
        baseline_ids = {finding["id"] for finding in baseline["findings"]}
        improved_ids = {finding["id"] for finding in improved["findings"]}
        self.assertNotIn("repo-control-gap", baseline_ids)
        self.assertIn("repo-control-gap", improved_ids)
        self.assertGreaterEqual(len(improved_ids), len(baseline_ids))

    def test_empty_bundle_is_inconclusive_not_fake_clean(self) -> None:
        result = analyze_bundle("", "improved")
        self.assertEqual(result["findings"], [])
        self.assertIn("incomplete", result["summary"].lower())

    def test_low_signal_bundle_has_false_positive_control_eval(self) -> None:
        raw = Path("samples/gcp_low_signal_control_bundle.txt").read_text(encoding="utf-8")
        result = analyze_bundle(raw, "improved")
        self.assertEqual(result["findings"], [])
        evals = {item["name"]: item for item in result["evals"]}
        self.assertEqual(evals["false_positive_control"]["status"], "pass")
        self.assertEqual(evals["risk_signal_coverage"]["status"], "pass")

    def test_parser_extracts_iam_and_terraform_evidence(self) -> None:
        raw = '{"bindings":[{"role":"roles/owner","members":["user:a@example.com"]}]}\nmember = "allUsers"'
        evidence = parse_evidence_bundle(raw)
        kinds = {item.kind for item in evidence}
        self.assertIn("iam_policy", kinds)
        self.assertIn("terraform", kinds)

    def test_findings_reference_existing_evidence(self) -> None:
        raw = '{"bindings":[{"role":"roles/run.invoker","members":["allUsers"]}]}'
        evidence = parse_evidence_bundle(raw)
        findings = derive_findings(evidence, improved=True)
        evidence_ids = {item.id for item in evidence}
        self.assertTrue(findings)
        self.assertTrue(all(set(finding.evidence_ids).issubset(evidence_ids) for finding in findings))

    def test_adk_tool_uses_deterministic_triage(self) -> None:
        raw = '{"bindings":[{"role":"roles/run.invoker","members":["allUsers"]}]}'
        result = triage_evidence_tool(raw)
        self.assertIn("findings", result)
        self.assertTrue(any(finding["id"] == "public-cloud-resource" for finding in result["findings"]))

    def test_runtime_status_redacts_secret_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PHOENIX_API_KEY": "super-secret",
                "GOOGLE_CLOUD_PROJECT": "traceguard-prod",
                "PHOENIX_MCP_COMMAND": "npx -y @arizeai/phoenix-mcp@latest --apiKey super-secret",
                "TRACEGUARD_AUTH_TOKEN": "another-secret",
            },
        ):
            status = RuntimeConfig.from_env().public_status()
        self.assertTrue(status["phoenix_api_key_configured"])
        self.assertTrue(status["phoenix_mcp_command_configured"])
        self.assertTrue(status["traceguard_auth_configured"])
        self.assertNotIn("super-secret", str(status))
        self.assertNotIn("another-secret", str(status))

    def test_gemini_enabled_without_dependency_returns_warning_not_crash(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GOOGLE_CLOUD_PROJECT": "traceguard-prod",
                "ENABLE_GEMINI_SYNTHESIS": "true",
                "PHOENIX_API_KEY": "",
                "PHOENIX_COLLECTOR_ENDPOINT": "",
            },
        ), patch("traceguard.gemini_adapter._call_google_genai", side_effect=ImportError("google-genai")):
            result = analyze_bundle("{}", "improved")
        self.assertTrue(result["gemini"]["enabled"])
        self.assertFalse(result["gemini"]["ok"])
        self.assertIn("Gemini synthesis", [step["name"] for step in result["steps"]])

    def test_gemini_brief_without_evidence_ids_is_rejected(self) -> None:
        raw = '{"bindings":[{"role":"roles/run.invoker","members":["allUsers"]}]}'
        with patch.dict(
            os.environ,
            {
                "GOOGLE_CLOUD_PROJECT": "traceguard-prod",
                "ENABLE_GEMINI_SYNTHESIS": "true",
                "PHOENIX_API_KEY": "",
                "PHOENIX_COLLECTOR_ENDPOINT": "",
            },
        ), patch(
            "traceguard.gemini_adapter._call_google_genai",
            return_value="Confirmed public access exists and should be fixed.",
        ):
            result = analyze_bundle(raw, "improved")

        self.assertTrue(result["gemini"]["enabled"])
        self.assertFalse(result["gemini"]["ok"])
        self.assertEqual(result["gemini"]["validation_status"], "fail")
        self.assertGreater(result["gemini"]["rejected_claims"], 0)
        self.assertNotIn("Gemini Incident Commander Brief", result["report_markdown"])

    def test_gemini_brief_with_known_evidence_id_is_accepted(self) -> None:
        raw = '{"bindings":[{"role":"roles/run.invoker","members":["allUsers"]}]}'
        with patch.dict(
            os.environ,
            {
                "GOOGLE_CLOUD_PROJECT": "traceguard-prod",
                "ENABLE_GEMINI_SYNTHESIS": "true",
                "PHOENIX_API_KEY": "",
                "PHOENIX_COLLECTOR_ENDPOINT": "",
            },
        ), patch(
            "traceguard.gemini_adapter._call_google_genai",
            return_value="Priority: confirmed public access from iam-001. Immediate action: remove allUsers.",
        ):
            result = analyze_bundle(raw, "improved")

        self.assertTrue(result["gemini"]["ok"])
        self.assertEqual(result["gemini"]["validation_status"], "pass")
        self.assertEqual(result["gemini"]["rejected_claims"], 0)
        self.assertIn("Gemini Incident Commander Brief", result["report_markdown"])

    def test_analyze_json_accepts_utf8_bom(self) -> None:
        body = b'\xef\xbb\xbf{"evidence_text":"{}", "mode":"improved"}'
        result = analyze_json(body)
        self.assertIn(b'"mode": "improved"', result)

    def test_auth_sessions_are_signed_and_expire(self) -> None:
        config = AuthConfig("correct horse battery staple", session_seconds=60)
        session = issue_session(config, now=1_000, nonce="fixed")
        self.assertTrue(validate_session_cookie(f"{COOKIE_NAME}={session}", config, now=1_030))
        self.assertFalse(validate_session_cookie(f"{COOKIE_NAME}={session}", config, now=1_061))
        self.assertFalse(validate_session_cookie(f"{COOKIE_NAME}={session}tampered", config, now=1_030))

    def test_auth_token_compare_and_cookie_flags(self) -> None:
        config = AuthConfig("demo-token", session_seconds=600)
        session = issue_session(config, now=1_000, nonce="fixed")
        cookie = build_session_cookie(session, secure=True, max_age=config.session_seconds)
        self.assertTrue(verify_access_token("demo-token", config))
        self.assertFalse(verify_access_token("wrong-token", config))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)


if __name__ == "__main__":
    unittest.main()
