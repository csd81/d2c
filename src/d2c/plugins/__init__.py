"""Plugin system — one of 4 extensibility mechanisms. Paper Section 6.

Plugins contribute hooks, skills, commands, and subagent definitions.
Loaded at session start from three source tiers:
  1. Bundled: src/d2c/plugins/bundled/
  2. User: ~/.d2c/plugins/
  3. Project: .d2c/plugins/ (highest precedence)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PluginManifest:
    """A plugin's manifest.json deserialized into a typed object.

    Each plugin is a directory containing a manifest.json file.
    """

    name: str
    version: str
    description: str = ""
    hooks: list[dict] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    mcp_servers: list[dict] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    source: str = ""  # "bundled" | "user" | "project" — set by loader
    source_path: str = ""  # filesystem path to the plugin directory


@dataclass
class LoadedPlugin:
    """A plugin that has been loaded and validated."""

    manifest: PluginManifest
    hooks_registered: int = 0
    skills_loaded: int = 0
    agents_loaded: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0
