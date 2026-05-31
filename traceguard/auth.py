from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie

COOKIE_NAME = "traceguard_session"
DEFAULT_SESSION_SECONDS = 12 * 60 * 60
MAX_SESSION_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class AuthConfig:
    token: str
    session_seconds: int = DEFAULT_SESSION_SECONDS
    require_token: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.token) or self.require_token

    @property
    def ready(self) -> bool:
        return bool(self.token)


def auth_config_from_env() -> AuthConfig:
    return AuthConfig(
        token=os.getenv("TRACEGUARD_AUTH_TOKEN", "").strip(),
        session_seconds=_int_env("TRACEGUARD_AUTH_SESSION_SECONDS", DEFAULT_SESSION_SECONDS),
        require_token=_bool_env("TRACEGUARD_REQUIRE_AUTH", default=False),
    )


def verify_access_token(candidate: object, config: AuthConfig) -> bool:
    if not config.enabled:
        return True
    if not config.ready:
        return False
    if not isinstance(candidate, str):
        return False
    return hmac.compare_digest(candidate.strip(), config.token)


def issue_session(config: AuthConfig, now: float | None = None, nonce: str | None = None) -> str:
    issued_at = str(int(now if now is not None else time.time()))
    nonce = nonce or secrets.token_urlsafe(24)
    payload = f"{issued_at}.{nonce}"
    return f"{payload}.{_sign(payload, config.token)}"


def validate_session_cookie(cookie_header: str | None, config: AuthConfig, now: float | None = None) -> bool:
    if not config.enabled:
        return True
    if not config.ready:
        return False
    if not cookie_header:
        return False
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except CookieError:
        return False
    morsel = cookie.get(COOKIE_NAME)
    if morsel is None:
        return False
    return validate_session_value(morsel.value, config, now=now)


def validate_session_value(value: str, config: AuthConfig, now: float | None = None) -> bool:
    if not config.enabled:
        return True
    if not config.ready:
        return False
    parts = value.split(".")
    if len(parts) != 3:
        return False
    issued_at, nonce, signature = parts
    if not issued_at.isdigit() or not nonce:
        return False
    payload = f"{issued_at}.{nonce}"
    expected = _sign(payload, config.token)
    if not hmac.compare_digest(signature, expected):
        return False
    current_time = int(now if now is not None else time.time())
    age = current_time - int(issued_at)
    return -60 <= age <= config.session_seconds


def build_session_cookie(session_value: str, *, secure: bool, max_age: int) -> str:
    attributes = [
        f"{COOKIE_NAME}={session_value}",
        "HttpOnly",
        "Path=/",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def build_logout_cookie(*, secure: bool) -> str:
    attributes = [
        f"{COOKIE_NAME}=",
        "HttpOnly",
        "Path=/",
        "SameSite=Lax",
        "Max-Age=0",
    ]
    if secure:
        attributes.append("Secure")
    return "; ".join(attributes)


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(300, min(parsed, MAX_SESSION_SECONDS))


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
