"""Plugin manifest parsing and validation.

A plugin is a directory containing a manifest.json file with structure:
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "...",
  "hooks": [...],
  "skills": [...],
  "agents": [...],
  "dependencies": [...]
}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from d2c.plugins import PluginManifest

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = {"name", "version"}
KNOWN_FIELDS = {
    "name", "version", "description",
    "hooks", "skills", "commands", "agents",
    "mcp_servers", "dependencies",
}


def parse_manifest(plugin_dir: Path) -> PluginManifest | None:
    """Parse manifest.json from a plugin directory.

    Returns None if the manifest is missing, malformed, or invalid.
    Logs warnings for specific issues.
    """
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.exists():
        logger.debug("No manifest.json in %s, skipping", plugin_dir)
        return None

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON in %s: %s", manifest_path, e)
        return None
    except OSError as e:
        logger.warning("Cannot read %s: %s", manifest_path, e)
        return None

    if not isinstance(raw, dict):
        logger.warning("manifest.json in %s is not a JSON object", plugin_dir)
        return None

    # Validate required fields
    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        logger.warning(
            "Plugin in %s missing required fields: %s",
            plugin_dir, ", ".join(missing),
        )
        return None

    name = raw["name"]
    if not isinstance(name, str) or not name.strip():
        logger.warning("Plugin in %s has invalid name", plugin_dir)
        return None

    version = raw["version"]
    if not isinstance(version, str):
        logger.warning("Plugin '%s' has non-string version", name)
        return None

    # Warn about unknown fields
    unknown = set(raw.keys()) - KNOWN_FIELDS
    if unknown:
        logger.debug(
            "Plugin '%s' has unknown fields: %s",
            name, ", ".join(unknown),
        )

    # Parse and validate hook definitions
    hooks = _parse_hooks(raw.get("hooks", []), name)

    # Parse skills (list of skill file names relative to plugin dir)
    skills = _parse_string_list(raw.get("skills", []), "skills", name)

    # Parse commands
    commands = raw.get("commands", [])
    if not isinstance(commands, list):
        logger.warning("Plugin '%s': 'commands' must be a list", name)
        commands = []

    # Parse agents (list of agent definition file names)
    agents = _parse_string_list(raw.get("agents", []), "agents", name)

    # Parse MCP servers
    mcp_servers = raw.get("mcp_servers", [])
    if not isinstance(mcp_servers, list):
        logger.warning("Plugin '%s': 'mcp_servers' must be a list", name)
        mcp_servers = []

    # Parse dependencies
    dependencies = _parse_string_list(raw.get("dependencies", []), "dependencies", name)

    return PluginManifest(
        name=name,
        version=version,
        description=raw.get("description", ""),
        hooks=hooks,
        skills=skills,
        commands=commands,
        agents=agents,
        mcp_servers=mcp_servers,
        dependencies=dependencies,
    )


def _parse_hooks(raw_hooks: list, plugin_name: str) -> list[dict]:
    """Validate hook definitions in the manifest."""
    if not isinstance(raw_hooks, list):
        logger.warning("Plugin '%s': 'hooks' must be a list", plugin_name)
        return []

    valid: list[dict] = []
    for i, hook in enumerate(raw_hooks):
        if not isinstance(hook, dict):
            logger.warning(
                "Plugin '%s': hook #%d is not an object, skipping",
                plugin_name, i + 1,
            )
            continue
        if "event" not in hook:
            logger.warning(
                "Plugin '%s': hook #%d missing 'event' field, skipping",
                plugin_name, i + 1,
            )
            continue
        valid.append(hook)

    return valid


def _parse_string_list(raw: list, field_name: str, plugin_name: str) -> list[str]:
    """Validate a list of strings from manifest."""
    if not isinstance(raw, list):
        logger.warning(
            "Plugin '%s': '%s' must be a list", plugin_name, field_name,
        )
        return []

    result: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            logger.warning(
                "Plugin '%s': %s[%d] is not a string, skipping",
                plugin_name, field_name, i,
            )
            continue
        result.append(item)

    return result
