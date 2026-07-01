"""Structured audit logging with redaction and correlation (Phase 44).

Emits one JSON object per line to an audit log (default ~/.d2c/logs/audit.jsonl,
opt-in via D2C_AUDIT_LOG=1). Events are correlated by session_id / turn_id /
tool_call_id. Secrets are redacted centrally; full prompts and tool outputs are
NOT logged unless explicitly enabled.

This module imports nothing from the rest of d2c (no import cycles). Call sites
use the cheap module-level `audit(...)`, which is a no-op when logging is off.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REDACTED = "[REDACTED]"
_MAX_STR = 500

# Field names whose values are always redacted.
_SECRET_KEY_NAMES = frozenset({
    "deepseek_api_key", "d2c_websearch_api_key", "anthropic_api_key",
    "api_key", "apikey", "authorization", "x-api-key", "x-subscription-token",
    "token", "password", "secret", "cookie", "set-cookie",
})

# Value shapes that look like secrets regardless of field name.
_SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|tvly-[A-Za-z0-9_\-]{6,})")

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _known_secret_values() -> list[str]:
    """Literal secret values from the environment, to redact even when they
    don't match a known prefix shape."""
    vals: list[str] = []
    for name in ("DEEPSEEK_API_KEY", "D2C_WEBSEARCH_API_KEY", "ANTHROPIC_API_KEY"):
        v = os.environ.get(name)
        if v and len(v) >= 8:
            vals.append(v)
    return vals


def redact(value: Any, _key: str | None = None) -> Any:
    """Recursively redact secrets from a value. Redacts by field name, by
    value shape (sk-.../tvly-...), and by literal env secret values; truncates
    long strings."""
    if _key is not None and str(_key).lower() in _SECRET_KEY_NAMES:
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        s = value
        for secret in _known_secret_values():
            if secret in s:
                s = s.replace(secret, REDACTED)
        s = _SECRET_VALUE_RE.sub(REDACTED, s)
        if len(s) > _MAX_STR:
            s = s[:_MAX_STR] + "... [truncated]"
        return s
    return value


class AuditLogger:
    def __init__(
        self,
        path: str | os.PathLike | None,
        enabled: bool,
        level: str = "INFO",
        log_prompts: bool = False,
        log_tool_outputs: bool = False,
    ):
        self.path = Path(path).expanduser() if path else None
        self.enabled = bool(enabled) and self.path is not None
        self.level = (level or "INFO").upper()
        self.log_prompts = log_prompts
        self.log_tool_outputs = log_tool_outputs
        self._context: dict[str, Any] = {}
        self._lock = threading.Lock()
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.enabled = False

    def set_context(self, **fields: Any) -> None:
        """Attach correlation fields (session_id, cwd, model, permission_mode)
        merged into every subsequent event."""
        for k, v in fields.items():
            if v is not None:
                self._context[k] = v

    def _level_ok(self, level: str) -> bool:
        return _LEVELS.get(level.upper(), 20) >= _LEVELS.get(self.level, 20)

    def emit(self, event: str, level: str = "INFO", **fields: Any) -> None:
        if not self.enabled or not self._level_ok(level):
            return
        record: dict[str, Any] = {"ts": _now_iso(), "level": level.upper(), "event": event}
        record.update(self._context)
        for k, v in fields.items():
            if v is not None:
                record[k] = redact(v, k)
        try:
            line = json.dumps(record, default=str)
        except (TypeError, ValueError):
            line = json.dumps({"ts": _now_iso(), "level": "ERROR", "event": "log_serialize_error",
                               "orig_event": event})
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except OSError:
            pass

    @classmethod
    def from_config(cls, config: Any) -> "AuditLogger":
        return cls(
            path=getattr(config, "audit_log_path", "") or None,
            enabled=getattr(config, "audit_log_enabled", False),
            level=getattr(config, "log_level", "INFO"),
            log_prompts=getattr(config, "log_prompts", False),
            log_tool_outputs=getattr(config, "log_tool_outputs", False),
        )


# ── Module-level global + convenience API ─────────────────────────────

_logger: AuditLogger | None = None


def set_audit_logger(logger: AuditLogger | None) -> None:
    global _logger
    _logger = logger


def get_audit_logger() -> AuditLogger | None:
    return _logger


def set_context(**fields: Any) -> None:
    if _logger is not None:
        _logger.set_context(**fields)


def audit(event: str, level: str = "INFO", **fields: Any) -> None:
    """Emit an audit event. No-op when logging is disabled/unconfigured."""
    lg = _logger
    if lg is not None:
        lg.emit(event, level=level, **fields)


def logs_tool_outputs() -> bool:
    return bool(_logger and _logger.log_tool_outputs)


def logs_prompts() -> bool:
    return bool(_logger and _logger.log_prompts)
