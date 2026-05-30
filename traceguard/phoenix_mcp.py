from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from typing import BinaryIO

from .config import RuntimeConfig
from .observability import TraceContext

MCP_PROTOCOL_VERSION = "2024-11-05"
PHOENIX_MCP_PACKAGE = "@arizeai/phoenix-mcp"
PINNED_PACKAGE_PATTERN = re.compile(r"^@arizeai/phoenix-mcp@\d+\.\d+\.\d+$")
MAX_MCP_MESSAGE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class PhoenixMcpResult:
    status: str
    summary: str
    tool_names: tuple[str, ...] = ()
    queried_tool_names: tuple[str, ...] = ()
    resource_counts: dict[str, int] = field(default_factory=dict)
    query_error: str = ""
    error: str = ""
    attempted: bool = False
    command_configured: bool = False

    @property
    def step_status(self) -> str:
        if self.status in {"ok", "discovery_only", "local_replay", "command_not_configured"}:
            return "complete"
        return "warn"

    def public_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["tool_count"] = len(self.tool_names)
        data["queried_tool_count"] = len(self.queried_tool_names)
        return data


@dataclass(frozen=True)
class McpRuntimeInspection:
    tool_names: tuple[str, ...]
    queried_tool_names: tuple[str, ...] = ()
    resource_counts: dict[str, int] = field(default_factory=dict)
    query_error: str = ""


def inspect_phoenix_mcp(
    context: TraceContext,
    *,
    improved: bool,
    config: RuntimeConfig | None = None,
) -> PhoenixMcpResult:
    runtime = config or RuntimeConfig.from_env()
    command_configured = bool(runtime.phoenix_mcp_command)

    if not context.phoenix_enabled:
        if improved:
            return PhoenixMcpResult(
                status="local_replay",
                command_configured=command_configured,
                summary=(
                    f"Local Phoenix simulated demo/replay guidance for project {context.phoenix_project}: "
                    "no live MCP trace query was performed because Phoenix tracing is not configured. Configure "
                    "PHOENIX_API_KEY or PHOENIX_COLLECTOR_ENDPOINT to send OTEL spans, then set "
                    "PHOENIX_MCP_COMMAND for live MCP tool discovery."
                ),
            )
        return PhoenixMcpResult(
            status="local_replay",
            command_configured=command_configured,
            summary=(
                f"Phoenix trace context prepared locally for project {context.phoenix_project}. "
                "This is simulated demo/replay guidance only; set PHOENIX_API_KEY or "
                "PHOENIX_COLLECTOR_ENDPOINT to send OTEL spans."
            ),
        )

    if not context.tracing_ready:
        detail = f": {context.tracing_error}" if context.tracing_error else "."
        return PhoenixMcpResult(
            status="tracing_not_ready",
            command_configured=command_configured,
            summary=(
                "Phoenix settings are present, but OTEL tracing is not live yet"
                f"{detail} No live MCP trace query was performed; use local replay guidance until tracing is ready."
            ),
            error=context.tracing_error,
        )

    if not runtime.phoenix_mcp_command:
        return PhoenixMcpResult(
            status="command_not_configured",
            command_configured=False,
            summary=(
                f"Phoenix OTEL is live for project {context.phoenix_project}, but no MCP command is configured. "
                "Set PHOENIX_MCP_COMMAND to launch the Phoenix MCP server, discover tools, and attempt read-only "
                "project/trace queries during the run."
            ),
        )

    try:
        inspection = _inspect_mcp_runtime(runtime)
    except Exception as exc:  # pragma: no cover - exact server failures are environment-specific
        error = _safe_error(exc)
        return PhoenixMcpResult(
            status="error",
            attempted=True,
            command_configured=True,
            error=error,
            summary=(
                f"Phoenix MCP query was attempted against {context.mcp_server}, but tool discovery failed: "
                f"{error}. OTEL trace delivery remains separate and should be checked in Phoenix."
            ),
        )

    tool_names = inspection.tool_names
    preview = ", ".join(tool_names[:6]) if tool_names else "no tools returned"
    extra = "" if len(tool_names) <= 6 else f", plus {len(tool_names) - 6} more"
    if inspection.queried_tool_names:
        query_bits = ", ".join(
            f"{name}={inspection.resource_counts.get(name, 0)}"
            for name in inspection.queried_tool_names
        )
        summary = (
            f"Phoenix MCP query succeeded against {context.mcp_server}: initialized an MCP session, discovered "
            f"{len(tool_names)} tools ({preview}{extra}), and queried read-only Phoenix data ({query_bits})."
        )
        status = "ok"
    else:
        query_detail = f" Read-only trace/eval query did not complete: {inspection.query_error}." if inspection.query_error else ""
        summary = (
            f"Phoenix MCP discovery succeeded against {context.mcp_server}: initialized an MCP session and discovered "
            f"{len(tool_names)} tools ({preview}{extra}).{query_detail}"
        )
        status = "discovery_only"
    return PhoenixMcpResult(
        status=status,
        attempted=True,
        command_configured=True,
        tool_names=tool_names,
        queried_tool_names=inspection.queried_tool_names,
        resource_counts=inspection.resource_counts,
        query_error=inspection.query_error,
        summary=summary,
    )


def _inspect_mcp_runtime(config: RuntimeConfig) -> McpRuntimeInspection:
    command = _split_command(config.phoenix_mcp_command)
    env = _mcp_environment(config)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("MCP server did not expose stdio pipes")

    try:
        _send_mcp_message(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "traceguard", "version": "0.1.0"},
                },
            },
        )
        init_response = _read_mcp_message_with_timeout(process.stdout, config.phoenix_mcp_timeout_seconds)
        _raise_on_mcp_error(init_response, "initialize")

        _send_mcp_message(process.stdin, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _send_mcp_message(process.stdin, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools_response = _read_mcp_message_with_timeout(process.stdout, config.phoenix_mcp_timeout_seconds)
        _raise_on_mcp_error(tools_response, "tools/list")
        tools = tools_response.get("result", {}).get("tools", [])
        if not isinstance(tools, list):
            raise RuntimeError("MCP tools/list returned an unexpected shape")
        tool_dicts = tuple(tool for tool in tools if isinstance(tool, dict) and tool.get("name"))
        tool_names = tuple(sorted(str(tool.get("name", "")) for tool in tool_dicts))
        queried_tool_names, resource_counts, query_error = _attempt_read_queries(
            process,
            tool_dicts,
            config,
            start_request_id=3,
        )
        return McpRuntimeInspection(
            tool_names=tool_names,
            queried_tool_names=queried_tool_names,
            resource_counts=resource_counts,
            query_error=query_error,
        )
    finally:
        _stop_process(process)


def _attempt_read_queries(
    process: subprocess.Popen[bytes],
    tools: tuple[dict[str, object], ...],
    config: RuntimeConfig,
    *,
    start_request_id: int,
) -> tuple[tuple[str, ...], dict[str, int], str]:
    tool_by_name = {str(tool.get("name")): tool for tool in tools}
    queried: list[str] = []
    counts: dict[str, int] = {}
    errors: list[str] = []
    request_id = start_request_id
    project_hint: dict[str, str] = {}

    for tool_name in ("list-projects", "list-traces"):
        tool = tool_by_name.get(tool_name)
        if not tool:
            continue
        arguments = _arguments_for_tool(tool, config, project_hint)
        if arguments is None:
            errors.append(f"{tool_name} required unsupported arguments")
            continue
        try:
            result = _call_mcp_tool(process, request_id, tool_name, arguments, config.phoenix_mcp_timeout_seconds)
        except Exception as exc:  # pragma: no cover - environment-specific MCP schemas/failures
            errors.append(f"{tool_name}: {_safe_error(exc)}")
            request_id += 1
            continue
        queried.append(tool_name)
        counts[tool_name] = _estimate_resource_count(result)
        if tool_name == "list-projects":
            project_hint = _project_hint_from_result(result, config)
        request_id += 1

    return tuple(queried), counts, "; ".join(errors)[:500]


def _call_mcp_tool(
    process: subprocess.Popen[bytes],
    request_id: int,
    name: str,
    arguments: dict[str, object],
    timeout_seconds: float,
) -> dict[str, object]:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("MCP server did not expose stdio pipes")
    _send_mcp_message(
        process.stdin,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    response = _read_mcp_message_with_timeout(process.stdout, timeout_seconds)
    _raise_on_mcp_error(response, f"tools/call {name}")
    result = response.get("result", {})
    if not isinstance(result, dict):
        raise RuntimeError(f"MCP tools/call {name} returned an unexpected shape")
    return result


def _arguments_for_tool(
    tool: dict[str, object],
    config: RuntimeConfig,
    project_hint: dict[str, str],
) -> dict[str, object] | None:
    schema = tool.get("inputSchema")
    properties: dict[str, object] = {}
    required: set[str] = set()
    if isinstance(schema, dict):
        raw_properties = schema.get("properties")
        if isinstance(raw_properties, dict):
            properties = raw_properties
        raw_required = schema.get("required")
        if isinstance(raw_required, list):
            required = {str(item) for item in raw_required}

    arguments: dict[str, object] = {}
    for name in set(properties) | required:
        lowered = name.lower()
        if lowered in {"projectname", "project_name", "project"}:
            arguments[name] = project_hint.get("projectName") or config.phoenix_project_name
        elif lowered in {"projectid", "project_id"}:
            project_id = project_hint.get("projectId")
            if project_id:
                arguments[name] = project_id
        elif lowered in {"limit", "first", "pagesize", "page_size", "maxresults", "max_results"}:
            arguments[name] = 5

    missing_required = required - set(arguments)
    if missing_required:
        return None
    return arguments


def _estimate_resource_count(result: dict[str, object]) -> int:
    for value in _json_like_values(result):
        count = _resource_count_from_value(value)
        if count is not None:
            return count
    return 1 if result else 0


def _resource_count_from_value(value: object) -> int | None:
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        return None
    for key in ("projects", "traces", "spans", "sessions", "datasets", "experiments", "data", "items", "nodes"):
        child = value.get(key)
        if isinstance(child, list):
            return len(child)
    return None


def _project_hint_from_result(result: dict[str, object], config: RuntimeConfig) -> dict[str, str]:
    fallback: dict[str, str] = {}
    for value in _json_like_values(result):
        for node in _iter_dicts(value):
            name = str(node.get("name") or node.get("projectName") or node.get("project_name") or "")
            project_id = str(node.get("id") or node.get("projectId") or node.get("project_id") or "")
            if not name and not project_id:
                continue
            hint = {"projectName": name or config.phoenix_project_name}
            if project_id:
                hint["projectId"] = project_id
            if name == config.phoenix_project_name:
                return hint
            if not fallback:
                fallback = hint
    return fallback


def _json_like_values(result: dict[str, object]) -> tuple[object, ...]:
    values: list[object] = [result]
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                try:
                    values.append(json.loads(text))
                except json.JSONDecodeError:
                    continue
    return tuple(values)


def _iter_dicts(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _split_command(command: str) -> list[str]:
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        raise RuntimeError(f"invalid PHOENIX_MCP_COMMAND: {exc}") from exc
    if not parts:
        raise RuntimeError("PHOENIX_MCP_COMMAND is empty")
    _validate_command(parts)
    return parts


def _validate_command(parts: list[str]) -> None:
    executable = os.path.basename(parts[0]).lower()
    if executable in {"npx", "npx.cmd"}:
        packages = [part for part in parts[1:] if part.startswith(f"{PHOENIX_MCP_PACKAGE}@")]
        if not packages:
            raise RuntimeError("PHOENIX_MCP_COMMAND must run the official @arizeai/phoenix-mcp package")
        if not any(PINNED_PACKAGE_PATTERN.match(part) for part in packages):
            raise RuntimeError("PHOENIX_MCP_COMMAND must pin @arizeai/phoenix-mcp to an exact version")
        return
    if executable in {"phoenix-mcp", "phoenix-mcp.cmd"}:
        return
    raise RuntimeError("PHOENIX_MCP_COMMAND executable is not allowlisted")


def _mcp_environment(config: RuntimeConfig) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PHOENIX_BASE_URL", config.phoenix_base_url)
    env.setdefault("PHOENIX_HOST", config.phoenix_base_url)
    env.setdefault("PHOENIX_PROJECT_NAME", config.phoenix_project_name)
    env.setdefault("PHOENIX_PROJECT", config.phoenix_project_name)
    return env


def _send_mcp_message(stream: BinaryIO, message: dict[str, object]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    stream.write(payload + b"\n")
    stream.flush()


def _read_mcp_message_with_timeout(stream: BinaryIO, timeout_seconds: float) -> dict[str, object]:
    messages: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            messages.put((True, _read_mcp_message(stream)))
        except Exception as exc:  # pragma: no cover - exercised through timeout/server failures
            messages.put((False, exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        ok, value = messages.get(timeout=max(timeout_seconds, 0.1))
    except queue.Empty as exc:
        raise TimeoutError(f"MCP server did not respond within {timeout_seconds:g}s") from exc
    if ok:
        return value  # type: ignore[return-value]
    raise value  # type: ignore[misc]


def _read_mcp_message(stream: BinaryIO) -> dict[str, object]:
    prefix = bytearray()
    while True:
        chunk = stream.read(1)
        if not chunk:
            raise EOFError("MCP server closed stdout before sending a response")
        prefix.extend(chunk)
        if b"\r\n\r\n" in prefix:
            return _read_content_length_message(stream, bytes(prefix))
        if chunk == b"\n":
            line = bytes(prefix).strip()
            if not line:
                prefix.clear()
                continue
            if line.startswith(b"{"):
                decoded = json.loads(line.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise RuntimeError("MCP response was not a JSON object")
                return decoded
        if len(prefix) > MAX_MCP_MESSAGE_BYTES:
            raise RuntimeError("MCP response exceeded 4 MiB before a message boundary")


def _read_content_length_message(stream: BinaryIO, prefix: bytes) -> dict[str, object]:
    header_bytes, body_prefix = prefix.split(b"\r\n\r\n", 1)
    content_length = _content_length(header_bytes)
    body = bytearray(body_prefix)
    while len(body) < content_length:
        chunk = stream.read(content_length - len(body))
        if not chunk:
            raise EOFError("MCP server closed stdout before completing a response")
        body.extend(chunk)
    decoded = json.loads(bytes(body[:content_length]).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("MCP response was not a JSON object")
    return decoded


def _content_length(header_bytes: bytes) -> int:
    for raw_line in header_bytes.decode("ascii", errors="replace").split("\r\n"):
        key, _, value = raw_line.partition(":")
        if key.lower() == "content-length":
            try:
                return int(value.strip())
            except ValueError as exc:
                raise RuntimeError("MCP response had an invalid Content-Length") from exc
    raise RuntimeError("MCP response missing Content-Length")


def _raise_on_mcp_error(message: dict[str, object], method: str) -> None:
    if "error" not in message:
        return
    error = message["error"]
    if isinstance(error, dict):
        detail = error.get("message") or error.get("code") or error
    else:
        detail = error
    raise RuntimeError(f"MCP {method} failed: {detail}")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    secret = os.getenv("PHOENIX_API_KEY", "")
    if secret:
        message = message.replace(secret, "[redacted]")
    return message[:500]
