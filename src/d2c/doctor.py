"""Config doctor / diagnostics (Phase 47).

`python -m d2c --doctor` runs local, offline checks and prints actionable
PASS/WARN/FAIL results. No model/API calls by default; no secrets are printed.
Exit code is 1 only when at least one check FAILs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class DoctorResult:
    name: str
    status: str  # "pass" | "warn" | "fail"
    message: str
    fix: str | None = None


def _p(name: str, message: str, fix: str | None = None) -> DoctorResult:
    return DoctorResult(name, "pass", message, fix)


def _w(name: str, message: str, fix: str | None = None) -> DoctorResult:
    return DoctorResult(name, "warn", message, fix)


def _f(name: str, message: str, fix: str | None = None) -> DoctorResult:
    return DoctorResult(name, "fail", message, fix)


# ── Individual checks (pure: inputs in, DoctorResult out) ─────────────


def check_python() -> DoctorResult:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 11):
        return _p("Python", ver)
    return _f("Python", f"{ver} (need >= 3.11)", fix="Install Python 3.11 or newer.")


def check_imports() -> DoctorResult:
    mods = ["d2c", "d2c.loop", "d2c.tools.pool", "d2c.permissions", "d2c.observability"]
    try:
        import importlib

        for m in mods:
            importlib.import_module(m)
    except Exception as e:  # noqa: BLE001 - report class only, not a traceback
        return _f(
            "Imports",
            f"{type(e).__name__} importing core modules",
            fix='Reinstall with: pip install -e ".[dev]"',
        )
    return _p("Imports", "core modules import")


def check_deepseek(config: Any) -> DoctorResult:
    model = getattr(config, "model", "?")
    if getattr(config, "deepseek_api_key", None):
        return _p("DeepSeek", f"model={model}, key set")
    return _w("DeepSeek", "DEEPSEEK_API_KEY is not set", fix="export DEEPSEEK_API_KEY=sk-...")


def check_websearch(config: Any) -> DoctorResult:
    from d2c.tools.web_search import _PROVIDERS

    provider = getattr(config, "websearch_provider", "") or ""
    key = getattr(config, "websearch_api_key", None)
    base_url = getattr(config, "websearch_base_url", "") or ""
    if not provider and not key and not base_url:
        return _w(
            "WebSearch",
            "unconfigured (WebSearch tool disabled)",
            fix="export D2C_WEBSEARCH_PROVIDER=tavily D2C_WEBSEARCH_API_KEY=tvly-...",
        )
    effective = provider or "tavily"
    cls = _PROVIDERS.get(effective)
    if cls is None:
        return _f(
            "WebSearch",
            f"unsupported provider '{effective}'",
            fix=f"Use one of: {', '.join(sorted(_PROVIDERS))}",
        )
    # Phase 58: provider-specific requirement (searxng needs a base URL
    # instead of an API key; tavily/brave need the key).
    if cls.requires_base_url and not base_url:
        return _w(
            "WebSearch",
            f"provider={effective} but no base URL",
            fix="export D2C_WEBSEARCH_BASE_URL=http://localhost:8080",
        )
    if cls.requires_api_key and not key:
        return _w(
            "WebSearch",
            f"provider={effective} but no API key",
            fix="export D2C_WEBSEARCH_API_KEY=tvly-...",
        )
    detail = f"provider={effective}"
    if cls.requires_base_url:
        detail += f", base_url={base_url}"
    return _p("WebSearch", detail)


def check_websearch_live(config: Any) -> DoctorResult:
    """Optional live probe (only run with --doctor-live). Never prints the key."""
    import asyncio

    from d2c.tools.web_search import (
        _PROVIDERS,
        WebSearchAuthError,
        WebSearchError,
        WebSearchRateLimitError,
        WebSearchTimeoutError,
        _make_provider,
    )

    provider_name = getattr(config, "websearch_provider", "") or "tavily"
    key = getattr(config, "websearch_api_key", None) or ""
    base_url = getattr(config, "websearch_base_url", "") or ""

    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        return _f("WebSearch (live)", f"unsupported provider '{provider_name}'")
    if cls.requires_base_url and not base_url:
        return _w("WebSearch (live)", "skipped — no base URL configured")
    if cls.requires_api_key and not key:
        return _w("WebSearch (live)", "skipped — not configured")

    provider = _make_provider(provider_name, key, 15.0, base_url)
    if provider is None:
        return _f("WebSearch (live)", f"unsupported provider '{provider_name}'")
    try:
        results = asyncio.run(provider.search("d2c doctor connectivity check", max_results=1))
    except WebSearchAuthError:
        return _f("WebSearch (live)", "authentication failed (check API key)")
    except WebSearchRateLimitError:
        return _w("WebSearch (live)", "rate limited")
    except WebSearchTimeoutError:
        return _w("WebSearch (live)", "timed out")
    except WebSearchError as e:
        return _f("WebSearch (live)", f"failed: {type(e).__name__}")
    return _p("WebSearch (live)", f"ok ({len(results)} result)")


def check_git() -> DoctorResult:
    if not shutil.which("git"):
        return _w(
            "Git",
            "git not found on PATH",
            fix="Install git to enable GitStatus/GitDiff/worktree tools.",
        )
    try:
        out = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=5)
        return _p("Git", out.stdout.strip() or "available")
    except (OSError, subprocess.SubprocessError):
        return _w("Git", "git present but not runnable")


def check_workspace(cwd: Path) -> DoctorResult:
    if not cwd.exists() or not cwd.is_dir():
        return _f("Workspace", f"cwd does not exist: {cwd}")
    if not os.access(cwd, os.R_OK):
        return _f("Workspace", f"cwd not readable: {cwd}")
    if not os.access(cwd, os.W_OK):
        return _w("Workspace", f"cwd not writable: {cwd} (file edits/checkpoints will fail)")
    is_repo = False
    if shutil.which("git"):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            is_repo = r.returncode == 0 and r.stdout.strip() == "true"
        except (OSError, subprocess.SubprocessError):
            is_repo = False
    return _p("Workspace", f"{cwd}" + (" (git repo)" if is_repo else " (not a git repo)"))


def check_trust(trusted: bool) -> DoctorResult:
    if trusted:
        return _p("Trust", "workspace is trusted; local .env/plugins/MCP/skills/memory enabled")
    return _w(
        "Trust",
        "workspace is untrusted; local plugins/MCP/skills/memory/.env are skipped",
        fix="Run with --trust to enable project-local extensions.",
    )


def check_sandbox(config: Any) -> DoctorResult:
    enabled = getattr(config, "sandbox_enabled", False)
    if not enabled:
        return _p("Sandbox", "disabled (default)")
    # Sandbox backend selection lives in SandboxConfig; the pool uses the
    # default (process) backend unless a config file overrides it.
    if not shutil.which("docker"):
        return _p(
            "Sandbox", "enabled (process backend; not a filesystem jail — see docs/security.md)"
        )
    return _p("Sandbox", "enabled (process backend; docker available for stronger isolation)")


def check_audit(config: Any) -> DoctorResult:
    if not getattr(config, "audit_log_enabled", False):
        return _p("Audit log", "disabled (default)")
    path = Path(getattr(config, "audit_log_path", "") or "").expanduser()
    if not path:
        return _f("Audit log", "enabled but no path configured")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(path.parent, os.W_OK)
    except OSError:
        writable = False
    if not writable:
        return _f(
            "Audit log",
            f"enabled but path not writable: {path.parent}",
            fix="Set D2C_AUDIT_LOG_PATH to a writable location.",
        )
    extras = []
    if getattr(config, "log_prompts", False):
        extras.append("prompts")
    if getattr(config, "log_tool_outputs", False):
        extras.append("tool-outputs")
    if extras:
        return _w(
            "Audit log",
            f"enabled; FULL {'/'.join(extras)} logging on (privacy risk)",
            fix="Unset D2C_LOG_PROMPTS / D2C_LOG_TOOL_OUTPUTS unless needed.",
        )
    return _p("Audit log", f"enabled -> {path}")


def check_mcp(cwd: Path, trusted: bool) -> DoctorResult:
    candidates = [cwd / ".d2c" / "mcp.json", Path.home() / ".d2c" / "mcp.json"]
    present = [p for p in candidates if p.exists()]
    if not present:
        return _p("MCP", "no mcp.json (none configured)")
    for p in present:
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return _f("MCP", f"malformed config {p}: {type(e).__name__}")
    project_cfg = (cwd / ".d2c" / "mcp.json") in present
    if project_cfg and not trusted:
        return _w("MCP", "project mcp.json present but skipped (untrusted workspace)")
    return _p("MCP", f"config parses ({len(present)} file(s))")


def check_skills_plugins(cwd: Path, trusted: bool) -> DoctorResult:
    from d2c.skills.loader import load_bundled_skills

    names = [s.name for s in load_bundled_skills()]
    if "commit" not in names:
        return _f(
            "Skills",
            "bundled skill 'commit' missing (broken package data)",
            fix="Reinstall; ensure d2c/skills/*.md ships in the wheel.",
        )
    local = cwd / ".d2c" / "skills"
    if local.is_dir() and not trusted:
        return _w("Skills", "bundled ok; local .d2c/skills skipped (untrusted workspace)")
    return _p("Skills", f"bundled ok ({', '.join(names)})")


def check_settings(cwd: Path, trusted: bool) -> DoctorResult:
    """Phase 60: layered settings (managed > user > project > local)."""
    from d2c.settings import load_settings

    merged = load_settings(cwd, trusted)

    if merged.errors:
        return _f(
            "Settings",
            f"{len(merged.errors)} malformed entr{'y' if len(merged.errors) == 1 else 'ies'}: "
            + "; ".join(str(e) for e in merged.errors[:3])
            + (" ..." if len(merged.errors) > 3 else ""),
            fix="Fix the listed settings.yaml file(s); malformed entries are skipped, not applied.",
        )

    scopes_loaded = [f.scope.name.lower() for f in merged.loaded_files]
    detail = f"scopes loaded: {', '.join(scopes_loaded) or 'none'}"
    if merged.sources:
        winners = ", ".join(f"{k}<-{v.name.lower()}" for k, v in merged.sources.items())
        detail += f"; {winners}"
    if merged.overridden_attempts:
        return _w(
            "Settings",
            f"{detail}; {len(merged.overridden_attempts)} override attempt(s) blocked by "
            "managed/higher-scope lock: " + "; ".join(str(a) for a in merged.overridden_attempts),
        )
    project_or_local_skipped = not trusted and (
        project_settings_exists(cwd) or local_settings_exists(cwd)
    )
    if project_or_local_skipped:
        detail += " (project/local settings present but skipped — untrusted workspace)"
    return _p("Settings", detail if scopes_loaded or merged.sources else "no settings files found")


def project_settings_exists(cwd: Path) -> bool:
    from d2c.settings import project_settings_path

    return project_settings_path(cwd).exists()


def local_settings_exists(cwd: Path) -> bool:
    from d2c.settings import local_settings_path

    return local_settings_path(cwd).exists()


# ── Orchestration + rendering ─────────────────────────────────────────


def run_doctor(config: Any, cwd: Path, trusted: bool, live: bool = False) -> list[DoctorResult]:
    results = [
        check_python(),
        check_imports(),
        check_deepseek(config),
        check_websearch(config),
        check_git(),
        check_workspace(cwd),
        check_trust(trusted),
        check_sandbox(config),
        check_audit(config),
        check_mcp(cwd, trusted),
        check_skills_plugins(cwd, trusted),
        check_settings(cwd, trusted),
    ]
    if live:
        results.append(check_websearch_live(config))
    return results


def summarize(results: list[DoctorResult]) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0}  # nosec B105: status labels, not a password
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def exit_code(results: list[DoctorResult]) -> int:
    return 1 if any(r.status == "fail" for r in results) else 0


def render_text(results: list[DoctorResult]) -> str:
    tag = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}  # nosec B105: display labels
    width = max((len(r.name) for r in results), default=0)
    lines = ["d2c doctor", ""]
    for r in results:
        lines.append(f"{tag[r.status]} {r.name.ljust(width)}  {r.message}")
        if r.fix and r.status != "pass":
            lines.append(f"     ↳ fix: {r.fix}")
    c = summarize(results)
    lines += ["", f"Summary: {c['pass']} passed, {c['warn']} warnings, {c['fail']} failed"]
    return "\n".join(lines)


def render_json(results: list[DoctorResult]) -> str:
    return json.dumps(
        {"summary": summarize(results), "results": [asdict(r) for r in results]},
        indent=2,
    )
