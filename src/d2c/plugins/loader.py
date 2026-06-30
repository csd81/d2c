"""Plugin discovery and loading from 3 source tiers.

Paper Section 6: "Hook sources include settings.json, plugins, and
managed policy at startup; skill hooks register dynamically on invocation."

Source tiers (later overrides earlier):
  1. Bundled: src/d2c/plugins/bundled/ — ship with d2c
  2. User: ~/.d2c/plugins/ — per-user plugins
  3. Project: .d2c/plugins/ — per-project plugins (highest precedence)

Each plugin is a directory containing a manifest.json file.
"""

from __future__ import annotations

import logging
from pathlib import Path

from d2c.plugins import LoadedPlugin, PluginManifest
from d2c.plugins.manifest import parse_manifest

logger = logging.getLogger(__name__)


class PluginLoader:
    """Discovers and loads plugins from all source tiers.

    Usage:
        loader = PluginLoader()
        plugins = loader.discover_and_load(cwd)
        for plugin in plugins:
            # register hooks, skills, agents, etc.
    """

    def __init__(self) -> None:
        self._bundled_dir = Path(__file__).resolve().parent / "bundled"
        self._user_dir = Path.home() / ".d2c" / "plugins"
        self._project_dir_name = ".d2c" / Path("plugins")

    def discover_all(self, cwd: Path | None = None) -> list[PluginManifest]:
        """Discover plugins from all sources without loading them.

        Project plugins override user plugins, which override bundled plugins
        with the same name.
        """
        project_dir = cwd or Path.cwd()
        manifests: dict[str, PluginManifest] = {}

        # Tier 1: Bundled (lowest precedence)
        for m in self._discover_from_dir(self._bundled_dir, "bundled"):
            manifests[m.name] = m

        # Tier 2: User (medium precedence)
        for m in self._discover_from_dir(self._user_dir, "user"):
            manifests[m.name] = m

        # Tier 3: Project (highest precedence) — only if trusted
        from d2c.trust import get_trust_gate
        if get_trust_gate().is_project_trusted:
            project_plugins_dir = project_dir / self._project_dir_name
            for m in self._discover_from_dir(project_plugins_dir, "project"):
                manifests[m.name] = m

        logger.info(
            "Discovered %d plugin(s): %s",
            len(manifests),
            [(m.name, m.source) for m in manifests.values()],
        )

        return list(manifests.values())

    def discover_and_load(
        self, cwd: Path | None = None,
    ) -> list[LoadedPlugin]:
        """Discover and validate all plugins. Returns loaded plugins.

        Plugins that fail to parse or have missing dependencies are
        logged as warnings but don't prevent other plugins from loading.
        """
        manifests = self.discover_all(cwd)
        loaded: list[LoadedPlugin] = []
        loaded_names: set[str] = set()

        for manifest in manifests:
            plugin = self._load_plugin(manifest, loaded_names)
            loaded.append(plugin)
            if plugin.is_valid:
                loaded_names.add(manifest.name)

        return loaded

    def _discover_from_dir(
        self, base_dir: Path, source: str,
    ) -> list[PluginManifest]:
        """Discover plugin directories in a base directory."""
        if not base_dir.is_dir():
            return []

        manifests: list[PluginManifest] = []
        for entry in sorted(base_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest = parse_manifest(entry)
            if manifest is None:
                continue
            manifest.source = source
            manifest.source_path = str(entry)
            manifests.append(manifest)

        return manifests

    def _load_plugin(
        self, manifest: PluginManifest, loaded_names: set[str],
    ) -> LoadedPlugin:
        """Validate and load a single plugin. Checks dependencies."""
        errors: list[str] = []

        # Check dependencies
        for dep in manifest.dependencies:
            if dep not in loaded_names:
                errors.append(
                    f"Dependency '{dep}' not found. "
                    f"Available: {', '.join(sorted(loaded_names)) if loaded_names else 'none'}"
                )

        return LoadedPlugin(
            manifest=manifest,
            hooks_registered=len(manifest.hooks) if not errors else 0,
            skills_loaded=len(manifest.skills) if not errors else 0,
            agents_loaded=len(manifest.agents) if not errors else 0,
            errors=errors,
        )
