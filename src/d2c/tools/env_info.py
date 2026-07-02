"""EnvInfo tool (Phase 51): structured runtime inspection without shelling out.

Read-only. Never exposes API keys or other secrets — only presence flags and
non-sensitive config (provider name, not key).
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")


class EnvInfoTool(Tool):
    name: ClassVar[str] = "EnvInfo"
    description: ClassVar[str] = (
        "Return structured runtime information (d2c/python versions, platform, cwd, "
        "git availability, configured model, WebSearch provider name, sandbox and "
        "audit-log flags). Read-only; never returns API keys. Prefer this over Bash "
        "for environment inspection."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    def __init__(self, cwd: Path | None = None):
        self._cwd = cwd or Path.cwd()

    async def execute(self, **kwargs: Any) -> ToolResult:
        from d2c import __version__

        info: dict[str, Any] = {
            "d2c_version": __version__,
            "python": platform.python_version(),
            "platform": platform.system(),
            "cwd": str(self._cwd),
            "git_available": bool(shutil.which("git")),
            "model": os.environ.get("D2C_MODEL", "deepseek-v4-flash"),
            "websearch_provider": os.environ.get("D2C_WEBSEARCH_PROVIDER", "").strip().lower()
            or None,
            "sandbox_enabled": _truthy("D2C_SANDBOX"),
            "audit_log_enabled": _truthy("D2C_AUDIT_LOG"),
        }
        lines = [
            f"d2c {info['d2c_version']} on Python {info['python']} ({info['platform']})",
            f"cwd: {info['cwd']}",
            f"git: {'available' if info['git_available'] else 'not found'}",
            f"model: {info['model']}",
            f"websearch: {info['websearch_provider'] or 'unconfigured'}",
            f"sandbox: {'on' if info['sandbox_enabled'] else 'off'}",
            f"audit log: {'on' if info['audit_log_enabled'] else 'off'}",
        ]
        return ToolResult(output="\n".join(lines), metadata=info)
