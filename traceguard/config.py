from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    google_cloud_project: str
    google_cloud_location: str
    google_genai_use_vertexai: bool
    gemini_model: str
    enable_gemini_synthesis: bool
    phoenix_project_name: str
    phoenix_base_url: str
    phoenix_collector_endpoint: str
    phoenix_api_key_configured: bool
    phoenix_mcp_server: str
    phoenix_mcp_command: str
    phoenix_mcp_timeout_seconds: float
    traceguard_auth_configured: bool
    traceguard_auth_required: bool
    traceguard_auth_session_seconds: int

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
        use_vertex = _bool_env("GOOGLE_GENAI_USE_VERTEXAI", default=bool(project))
        auth_session_seconds = _int_env("TRACEGUARD_AUTH_SESSION_SECONDS", default=12 * 60 * 60)
        return cls(
            google_cloud_project=project,
            google_cloud_location=location,
            google_genai_use_vertexai=use_vertex,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            enable_gemini_synthesis=_bool_env("ENABLE_GEMINI_SYNTHESIS", default=bool(project)),
            phoenix_project_name=os.getenv("PHOENIX_PROJECT_NAME", "traceguard-hackathon"),
            phoenix_base_url=os.getenv("PHOENIX_BASE_URL", "https://app.phoenix.arize.com"),
            phoenix_collector_endpoint=os.getenv("PHOENIX_COLLECTOR_ENDPOINT", ""),
            phoenix_api_key_configured=bool(os.getenv("PHOENIX_API_KEY")),
            phoenix_mcp_server=os.getenv("PHOENIX_MCP_SERVER", "@arizeai/phoenix-mcp"),
            phoenix_mcp_command=os.getenv("PHOENIX_MCP_COMMAND", "").strip(),
            phoenix_mcp_timeout_seconds=_float_env("PHOENIX_MCP_TIMEOUT_SECONDS", default=4.0),
            traceguard_auth_configured=bool(os.getenv("TRACEGUARD_AUTH_TOKEN", "").strip()),
            traceguard_auth_required=_bool_env("TRACEGUARD_REQUIRE_AUTH", default=False),
            traceguard_auth_session_seconds=auth_session_seconds,
        )

    def public_status(self) -> dict[str, str | bool | int | float]:
        return {
            "google_cloud_project": self.google_cloud_project,
            "google_cloud_location": self.google_cloud_location,
            "google_genai_use_vertexai": self.google_genai_use_vertexai,
            "gemini_model": self.gemini_model,
            "enable_gemini_synthesis": self.enable_gemini_synthesis,
            "phoenix_project_name": self.phoenix_project_name,
            "phoenix_base_url": self.phoenix_base_url,
            "phoenix_collector_endpoint": self.phoenix_collector_endpoint,
            "phoenix_api_key_configured": self.phoenix_api_key_configured,
            "phoenix_mcp_server": self.phoenix_mcp_server,
            "phoenix_mcp_command_configured": bool(self.phoenix_mcp_command),
            "phoenix_mcp_timeout_seconds": self.phoenix_mcp_timeout_seconds,
            "traceguard_auth_configured": self.traceguard_auth_configured,
            "traceguard_auth_required": self.traceguard_auth_required,
            "traceguard_auth_session_seconds": self.traceguard_auth_session_seconds,
        }


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default
