from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Mapping

from .config import RuntimeConfig


_TRACING_ATTEMPTED = False
_TRACING_READY = False
_TRACING_ERROR = ""
_TRACER = None

SpanAttribute = str | bool | int | float | tuple[str, ...] | tuple[bool, ...] | tuple[int, ...] | tuple[float, ...]


@dataclass(frozen=True)
class TraceContext:
    run_id: str
    phoenix_project: str
    phoenix_enabled: bool
    phoenix_collector_endpoint: str
    mcp_server: str
    tracing_ready: bool
    tracing_error: str


class SpanAnnotator:
    def __init__(self, span: object | None) -> None:
        self._span = span

    def set_attribute(self, key: str, value: object) -> None:
        if self._span is None:
            return
        coerced = _coerce_attribute(value)
        if coerced is not None:
            self._span.set_attribute(key, coerced)

    def add_event(self, name: str, attributes: Mapping[str, object] | None = None) -> None:
        if self._span is None or not hasattr(self._span, "add_event"):
            return
        self._span.add_event(name, _coerce_attributes(attributes or {}))


def new_trace_context() -> TraceContext:
    config = RuntimeConfig.from_env()
    phoenix_enabled = config.phoenix_api_key_configured or bool(config.phoenix_collector_endpoint)
    if phoenix_enabled:
        _initialize_phoenix(config)
    return TraceContext(
        run_id=str(uuid.uuid4()),
        phoenix_project=config.phoenix_project_name,
        phoenix_enabled=phoenix_enabled,
        phoenix_collector_endpoint=config.phoenix_collector_endpoint,
        mcp_server=config.phoenix_mcp_server,
        tracing_ready=_TRACING_READY,
        tracing_error=_TRACING_ERROR,
    )


@contextmanager
def trace_span(
    context: TraceContext,
    name: str,
    attributes: Mapping[str, object] | None = None,
) -> Iterator[SpanAnnotator]:
    start = time.perf_counter()
    if _TRACING_READY and _TRACER is not None:
        with _TRACER.start_as_current_span(name) as span:
            annotator = SpanAnnotator(span)
            annotator.set_attribute("traceguard.run_id", context.run_id)
            annotator.set_attribute("traceguard.phoenix_project", context.phoenix_project)
            for key, value in (attributes or {}).items():
                annotator.set_attribute(key, value)
            try:
                yield annotator
            finally:
                annotator.set_attribute("traceguard.duration_ms", round((time.perf_counter() - start) * 1000, 2))
        return
    try:
        yield SpanAnnotator(None)
    finally:
        _ = time.perf_counter() - start


def _coerce_attributes(attributes: Mapping[str, object]) -> dict[str, SpanAttribute]:
    coerced: dict[str, SpanAttribute] = {}
    for key, value in attributes.items():
        coerced_value = _coerce_attribute(value)
        if coerced_value is not None:
            coerced[key] = coerced_value
    return coerced


def _coerce_attribute(value: object) -> SpanAttribute | None:
    if value is None:
        return None
    scalar = _coerce_scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, (set, frozenset)):
        items = [_coerce_scalar(item) for item in sorted(value, key=str)]
    elif isinstance(value, (list, tuple)):
        items = [_coerce_scalar(item) for item in value]
    else:
        return str(value)

    clean_items = [item for item in items if item is not None]
    if not clean_items:
        return None
    if all(isinstance(item, bool) for item in clean_items):
        return tuple(clean_items)
    if all(isinstance(item, int) and not isinstance(item, bool) for item in clean_items):
        return tuple(clean_items)
    if all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in clean_items):
        return tuple(float(item) for item in clean_items)
    if all(isinstance(item, str) for item in clean_items):
        return tuple(clean_items)
    return tuple(str(item) for item in clean_items)


def _coerce_scalar(value: object) -> str | bool | int | float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)):
        return value
    return None


def _initialize_phoenix(config: RuntimeConfig) -> None:
    global _TRACING_ATTEMPTED, _TRACING_READY, _TRACING_ERROR, _TRACER
    if _TRACING_ATTEMPTED:
        return
    _TRACING_ATTEMPTED = True
    try:
        from opentelemetry import trace
        from phoenix.otel import register

        register(project_name=config.phoenix_project_name, auto_instrument=True, batch=True)
        _TRACER = trace.get_tracer("traceguard")
        _TRACING_READY = True
        _TRACING_ERROR = ""
    except ImportError as exc:
        _TRACING_ERROR = f"Production dependency missing: {exc.name or 'arize-phoenix-otel'}"
    except Exception as exc:  # pragma: no cover - requires live Phoenix configuration
        _TRACING_ERROR = f"Phoenix OTEL registration failed: {str(exc)[:500]}"
