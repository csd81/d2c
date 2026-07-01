"""PackageInfo tool (Phase 56): dependency-manifest summary.

Structured alternative to `cat pyproject.toml` / `cat package.json` via Bash.
Read-only; parses only, never executes build/install commands. Malformed
manifests are reported as errors rather than raised.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult

_MAX_LISTED = 30  # cap dependency/script listings to protect context


def _truncated(items: list[str]) -> tuple[list[str], bool]:
    if len(items) <= _MAX_LISTED:
        return items, False
    return items[:_MAX_LISTED], True


def _parse_pyproject(data: bytes) -> dict[str, Any]:
    parsed = tomllib.loads(data.decode("utf-8"))
    project = parsed.get("project") or {}
    deps = list(project.get("dependencies") or [])
    optional = parsed.get("project", {}).get("optional-dependencies") or {}
    for group in optional.values():
        deps.extend(group)
    scripts = list((project.get("scripts") or {}).keys())

    name = project.get("name")
    version = project.get("version")
    if name is None or version is None:
        # Poetry-style manifests keep metadata under [tool.poetry].
        poetry = parsed.get("tool", {}).get("poetry") or {}
        name = name or poetry.get("name")
        version = version or poetry.get("version")

    return {
        "manifest": "pyproject.toml",
        "name": name,
        "version": version,
        "description": project.get("description"),
        "dependencies": deps,
        "dependency_count": len(deps),
        "scripts": scripts,
    }


def _parse_package_json(data: bytes) -> dict[str, Any]:
    parsed = json.loads(data.decode("utf-8"))
    deps = list((parsed.get("dependencies") or {}).keys())
    dev_deps = list((parsed.get("devDependencies") or {}).keys())
    scripts = list((parsed.get("scripts") or {}).keys())

    return {
        "manifest": "package.json",
        "name": parsed.get("name"),
        "version": parsed.get("version"),
        "description": parsed.get("description"),
        "dependencies": deps + dev_deps,
        "dependency_count": len(deps) + len(dev_deps),
        "scripts": scripts,
    }


def _parse_requirements_txt(data: bytes) -> dict[str, Any]:
    lines = [
        line.strip()
        for line in data.decode("utf-8", errors="replace").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return {
        "manifest": "requirements.txt",
        "name": None,
        "version": None,
        "description": None,
        "dependencies": lines,
        "dependency_count": len(lines),
        "scripts": [],
    }


_MANIFESTS: list[tuple[str, Any]] = [
    ("pyproject.toml", _parse_pyproject),
    ("package.json", _parse_package_json),
    ("requirements.txt", _parse_requirements_txt),
]


class PackageInfoTool(Tool):
    name: ClassVar[str] = "PackageInfo"
    description: ClassVar[str] = (
        "Summarize a project's dependency manifest (pyproject.toml, package.json, "
        "or requirements.txt): package name, version, description, dependency "
        "count, and scripts/entry points. Read-only; prefer this over Bash for "
        "inspecting project metadata."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute directory to look in (default: cwd).",
            },
        },
        "required": [],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.READ
    is_concurrent_safe: ClassVar[bool] = True

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    async def execute(self, path: str = "", **kwargs: Any) -> ToolResult:
        if path:
            d = Path(path)
            if not d.is_absolute():
                return ToolResult(output=f"Error: path must be absolute, got: {path}", error=True)
        else:
            d = self._cwd

        if not d.is_dir():
            return ToolResult(output=f"Error: not a directory: {d}", error=True)

        for filename, parser in _MANIFESTS:
            manifest_path = d / filename
            if not manifest_path.is_file():
                continue
            try:
                data = manifest_path.read_bytes()
                info = parser(data)
            except Exception as e:
                return ToolResult(
                    output=f"Error parsing {filename}: {e}",
                    error=True,
                    metadata={"manifest": filename, "path": str(manifest_path)},
                )
            deps, deps_truncated = _truncated(info["dependencies"])
            scripts, scripts_truncated = _truncated(info["scripts"])

            lines = [
                f"Manifest: {info['manifest']} ({manifest_path})",
                f"Name: {info['name'] or '(unknown)'}",
                f"Version: {info['version'] or '(unknown)'}",
            ]
            if info["description"]:
                lines.append(f"Description: {info['description']}")
            lines.append(f"Dependencies ({info['dependency_count']}):")
            lines += [f"  {dep}" for dep in deps] or ["  (none)"]
            if deps_truncated:
                lines.append(f"  ... [truncated at {_MAX_LISTED}]")
            if scripts:
                lines.append(f"Scripts/entry points ({len(info['scripts'])}):")
                lines += [f"  {s}" for s in scripts]
                if scripts_truncated:
                    lines.append(f"  ... [truncated at {_MAX_LISTED}]")

            return ToolResult(
                output="\n".join(lines),
                metadata={
                    "manifest": info["manifest"],
                    "path": str(manifest_path),
                    "name": info["name"],
                    "version": info["version"],
                    "dependency_count": info["dependency_count"],
                    "dependencies_truncated": deps_truncated,
                },
            )

        return ToolResult(
            output=f"No dependency manifest found in {d} "
            f"(looked for {', '.join(name for name, _ in _MANIFESTS)}).",
            metadata={"found": False, "path": str(d)},
        )
