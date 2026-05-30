from __future__ import annotations

import json
import os
import queue
import re
import shlex
import subprocess
import threading
from dataclasses import asdict, dataclass
from typing import BinaryIO

from .config import RuntimeConfig
from .observability import TraceContext

MCP_PROTOCOL_VERSION = "2024-11-05"
PHOENIX_MCP_PACKAGE = "@arizeai/phoenix-mcp"
PINNED_PACKAGE_PATTERN = re.compile(r"^@arizeai/phoenix-mcp@\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class PhoenixMcpResult:
    status: str
    summary: str
    tool_names: tuple[str, ...] = ()
    error: str = ""
    attempted: bool = False
    command_configured: bool = False

    @property
    def step_status(self) -> str:
        if self.status in {"ok", "local_replay", "command_not_configured"}:
            return "complete"
        return "warn"

    def public_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["tool_count"] = len(self.tool_names)
        return data


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
                "Set PHOENIX_MCP_COMMAND to launch the Phoenix MCP server and perform read-only tools/list "
                "introspection during the run."
            ),
        )

    try:
        tool_names = _discover_mcp_tools(runtime)
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

    preview = ", ".join(tool_names[:6]) if tool_names else "no tools returned"
    extra = "" if len(tool_names) <= 6 else f", plus {len(tool_names) - 6} more"
    return PhoenixMcpResult(
        status="ok",
        attempted=True,
        command_configured=True,
        tool_names=tool_names,
        summary=(
            f"Phoenix MCP query succeeded against {context.mcp_server}: initialized an MCP session and discovered "
            f"{len(tool_names)} tools ({preview}{extra})."
        ),
    )


def _discover_mcp_tools(config: RuntimeConfig) -> tuple[str, ...]:
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
        return tuple(sorted(str(tool.get("name", "")) for tool in tools if isinstance(tool, dict) and tool.get("name")))
    finally:
        _stop_process(process)


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
    env.setdefault("PHOENIX_PROJECT_NAME", config.phoenix_project_name)
    return env


def _send_mcp_message(stream: BinaryIO, message: dict[str, object]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
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
    header = bytearray()
    while b"\r\n\r\n" not in header:
        chunk = stream.read(1)
        if not chunk:
            raise EOFError("MCP server closed stdout before sending a response")
        header.extend(chunk)
        if len(header) > 16_384:
            raise RuntimeError("MCP response headers exceeded 16 KiB")

    header_bytes, body_prefix = bytes(header).split(b"\r\n\r\n", 1)
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
