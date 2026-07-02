"""ConfigInfo tool (Phase 56): effective runtime configuration, without secrets.

Complements EnvInfo (raw runtime/platform facts) with the *decisions* d2c
made for this session — permission mode, trust, sandbox/audit/websearch
flags. Never returns API keys, only presence/provider names.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


class ConfigInfoTool(Tool):
    name: ClassVar[str] = "ConfigInfo"
    description: ClassVar[str] = (
        "Return the effective d2c configuration for this session: model, cwd, "
        "permission mode, workspace trust, and sandbox/audit/WebSearch flags. "
        "Read-only; never returns API keys. Prefer this over Bash/env inspection "
        "for questions about how d2c is currently configured."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    def __init__(self, cwd: Path | None = None, permission_mode: str = "default") -> None:
        self._cwd = cwd or Path.cwd()
        self._permission_mode = permission_mode

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            from d2c.trust import get_trust_gate

            trusted: bool | str = get_trust_gate().is_project_trusted
        except Exception:
            trusted = "unknown"

        info: dict[str, Any] = {
            "cwd": str(self._cwd),
            "model": os.environ.get("D2C_MODEL", "deepseek-v4-flash"),
            "thinking": os.environ.get("D2C_THINKING", "off").strip().lower(),
            "permission_mode": self._permission_mode,
            "trusted": trusted,
            "sandbox_enabled": _truthy("D2C_SANDBOX"),
            "audit_log_enabled": _truthy("D2C_AUDIT_LOG"),
            "websearch_provider": os.environ.get("D2C_WEBSEARCH_PROVIDER", "").strip().lower()
            or None,
            "websearch_configured": bool(os.environ.get("D2C_WEBSEARCH_API_KEY")),
            "cost_estimates_disabled": _truthy("D2C_DISABLE_COST_ESTIMATES"),
        }
        lines = [
            f"cwd: {info['cwd']}",
            f"model: {info['model']}",
            f"permission mode: {info['permission_mode']}",
            f"trusted: {info['trusted']}",
            f"sandbox: {'on' if info['sandbox_enabled'] else 'off'}",
            f"audit log: {'on' if info['audit_log_enabled'] else 'off'}",
            f"websearch: {info['websearch_provider'] or 'unconfigured'}"
            + (" (key set)" if info["websearch_configured"] else ""),
            f"cost estimates: {'disabled' if info['cost_estimates_disabled'] else 'enabled'}",
        ]
        return ToolResult(output="\n".join(lines), metadata=info)
