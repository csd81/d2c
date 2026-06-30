"""MCP server discovery — loads server configs from .d2c/mcp.json and env var.

Paper Section 6: "Load servers from .d2c/mcp.json and D2C_MCP_SERVERS
environment variable."

The D2C_MCP_SERVERS env var can be a JSON array of server configs or a
path to an mcp.json file.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from d2c.mcp import MCPServerConfig

logger = logging.getLogger(__name__)

# Regex for expanding ${VAR} and $VAR references in string values.
_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def _expand_env_vars(value: str) -> str:
    """Expand ${VAR} and $VAR references in a string using os.environ."""
    def _replace(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        return os.environ.get(var_name, match.group(0))
    return _ENV_VAR_RE.sub(_replace, value)


def _expand_env_in_config(config: dict[str, Any]) -> dict[str, Any]:
    """Recursively expand environment variable references in config values."""
    result: dict[str, Any] = {}
    for key, value in config.items():
        if isinstance(value, str):
            result[key] = _expand_env_vars(value)
        elif isinstance(value, dict):
            result[key] = _expand_env_in_config(value)
        elif isinstance(value, list):
            result[key] = [
                _expand_env_vars(v) if isinstance(v, str) else v
                for v in value
            ]
        else:
            result[key] = value
    return result


def _parse_single_server(name: str, raw: dict[str, Any]) -> MCPServerConfig:
    """Parse a single server entry into an MCPServerConfig."""
    expanded = _expand_env_in_config(raw)

    transport = expanded.get("transport", "stdio").lower()

    return MCPServerConfig(
        name=name,
        command=expanded.get("command"),
        args=expanded.get("args", []),
        url=expanded.get("url"),
        transport=transport,
        env=expanded.get("env", {}),
        timeout_ms=expanded.get("timeout_ms", 30_000),
        headers=expanded.get("headers", {}),
    )


def _load_from_json_file(file_path: Path) -> list[MCPServerConfig]:
    """Load MCP server configs from a JSON file."""
    if not file_path.exists():
        return []

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse %s: %s", file_path, e)
        return []

    if not isinstance(raw, dict):
        logger.warning("%s is not a JSON object, skipping", file_path)
        return []

    servers = raw.get("mcpServers", {})
    if not isinstance(servers, dict):
        logger.warning("mcpServers in %s is not an object, skipping", file_path)
        return []

    configs: list[MCPServerConfig] = []
    for name, server_raw in servers.items():
        if not isinstance(server_raw, dict):
            logger.warning("Server '%s' in %s is not an object, skipping", name, file_path)
            continue
        configs.append(_parse_single_server(name, server_raw))

    return configs


def _load_from_env_var() -> list[MCPServerConfig]:
    """Load MCP server configs from D2C_MCP_SERVERS environment variable.

    Can be:
    - A path to an mcp.json file
    - A JSON string with "mcpServers" object
    - A JSON array of server config objects
    """
    raw = os.environ.get("D2C_MCP_SERVERS", "").strip()
    if not raw:
        return []

    # Try as file path first
    if raw.endswith(".json"):
        path = Path(raw)
        if path.exists():
            return _load_from_json_file(path)
        path = Path(os.path.expanduser(raw))
        if path.exists():
            return _load_from_json_file(path)

    # Try as JSON string
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("D2C_MCP_SERVERS is not valid JSON and not a file path")
        return []

    if isinstance(data, list):
        configs: list[MCPServerConfig] = []
        for entry in data:
            if isinstance(entry, dict):
                name = entry.get("name", f"env-server-{len(configs)}")
                configs.append(_parse_single_server(name, entry))
        return configs

    if isinstance(data, dict):
        servers = data.get("mcpServers", {})
        configs = []
        for name, server_raw in servers.items():
            if isinstance(server_raw, dict):
                configs.append(_parse_single_server(name, server_raw))
        return configs

    return []


def discover_servers(cwd: Path | None = None) -> list[MCPServerConfig]:
    """Discover all MCP server configurations.

    Resolution order:
    1. Project .d2c/mcp.json (if cwd is set)
    2. Home ~/.d2c/mcp.json
    3. D2C_MCP_SERVERS environment variable

    Later sources add to, but don't override, earlier sources.
    Duplicate server names: first one wins.
    """
    project_dir = cwd or Path.cwd()
    all_configs: list[MCPServerConfig] = []
    seen_names: set[str] = set()

    def add_configs(configs: list[MCPServerConfig]) -> None:
        for cfg in configs:
            if cfg.name not in seen_names:
                seen_names.add(cfg.name)
                all_configs.append(cfg)

    # 1. Project mcp.json
    project_mcp_json = project_dir / ".d2c" / "mcp.json"
    add_configs(_load_from_json_file(project_mcp_json))

    # Walk up from cwd looking for mcp.json (like .env loading)
    current = project_dir.resolve()
    root = Path(current.anchor)
    dirs = [current]
    while current != root.parent:
        current = current.parent
        if current not in dirs:
            dirs.append(current)
    for d in reversed(dirs):
        mcp_json = d / ".d2c" / "mcp.json"
        if mcp_json.exists() and mcp_json != project_mcp_json:
            add_configs(_load_from_json_file(mcp_json))

    # 2. Home mcp.json
    home_mcp_json = Path.home() / ".d2c" / "mcp.json"
    add_configs(_load_from_json_file(home_mcp_json))

    # 3. D2C_MCP_SERVERS env var
    add_configs(_load_from_env_var())

    logger.info("Discovered %d MCP server(s): %s",
                 len(all_configs),
                 [c.name for c in all_configs])
    return all_configs
