"""Tests for Phase 13: Plugin System.

Covers: manifest parsing, discovery from 3 source tiers, precedence,
hook registration, skill loading, edge cases.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from d2c.plugins import LoadedPlugin, PluginManifest
from d2c.plugins.manifest import parse_manifest
from d2c.plugins.loader import PluginLoader
from d2c.hooks import HookDefinition, HookEvent, HookRegistry, HookType


# ── Manifest Parsing ──────────────────────────────────────────────────────

class TestManifestParsing:
    def test_valid_manifest(self):
        """Parse a complete valid manifest."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            manifest_json = {
                "name": "test-plugin",
                "version": "1.0.0",
                "description": "A test plugin",
                "hooks": [
                    {"event": "PostToolUse", "type": "command", "command": "echo hello"},
                ],
                "skills": ["commit.md"],
                "agents": ["reviewer"],
                "dependencies": [],
            }
            (plugin_dir / "manifest.json").write_text(json.dumps(manifest_json))

            manifest = parse_manifest(plugin_dir)
            assert manifest is not None
            assert manifest.name == "test-plugin"
            assert manifest.version == "1.0.0"
            assert manifest.description == "A test plugin"
            assert len(manifest.hooks) == 1
            assert manifest.hooks[0]["event"] == "PostToolUse"
            assert manifest.skills == ["commit.md"]
            assert manifest.agents == ["reviewer"]

    def test_missing_required_fields(self):
        """Manifest without name/version → None."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            (plugin_dir / "manifest.json").write_text(json.dumps({"hooks": []}))

            manifest = parse_manifest(plugin_dir)
            assert manifest is None

    def test_no_version(self):
        """Manifest with name but no version → None."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            (plugin_dir / "manifest.json").write_text(json.dumps({"name": "test"}))

            manifest = parse_manifest(plugin_dir)
            assert manifest is None

    def test_no_manifest_file(self):
        """Directory without manifest.json → None."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            manifest = parse_manifest(plugin_dir)
            assert manifest is None

    def test_malformed_json(self):
        """Invalid JSON → None with warning logged."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            (plugin_dir / "manifest.json").write_text("not json {{{")

            manifest = parse_manifest(plugin_dir)
            assert manifest is None

    def test_minimal_manifest(self):
        """Minimal manifest with just name and version."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            (plugin_dir / "manifest.json").write_text(json.dumps({
                "name": "minimal",
                "version": "0.1.0",
            }))

            manifest = parse_manifest(plugin_dir)
            assert manifest is not None
            assert manifest.name == "minimal"
            assert manifest.hooks == []
            assert manifest.skills == []
            assert manifest.agents == []

    def test_invalid_hooks_filtered(self):
        """Hook entries without 'event' field are skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            (plugin_dir / "manifest.json").write_text(json.dumps({
                "name": "bad-hooks",
                "version": "1.0",
                "hooks": [
                    {"event": "PostToolUse", "type": "command", "command": "ok"},
                    {"type": "command", "command": "missing-event"},  # no event
                    {"event": "PreToolUse", "type": "prompt", "prompt": "ok"},
                ],
            }))

            manifest = parse_manifest(plugin_dir)
            assert manifest is not None
            # Only the two valid hooks should remain
            assert len(manifest.hooks) == 2


# ── Plugin Discovery ──────────────────────────────────────────────────────

class TestPluginDiscovery:
    def test_discover_from_bundled_dir(self):
        """Discover a plugin from the bundled directory."""
        loader = PluginLoader()
        # Create a temporary bundled plugin
        bundled_dir = loader._bundled_dir
        bundled_dir.mkdir(parents=True, exist_ok=True)
        plugin_dir = bundled_dir / "test-bundled"
        plugin_dir.mkdir(exist_ok=True)
        (plugin_dir / "manifest.json").write_text(json.dumps({
            "name": "test-bundled",
            "version": "1.0.0",
            "description": "A bundled plugin",
        }))

        try:
            manifests = loader._discover_from_dir(bundled_dir, "bundled")
            assert len(manifests) == 1
            assert manifests[0].name == "test-bundled"
            assert manifests[0].source == "bundled"
        finally:
            # Cleanup
            import shutil
            shutil.rmtree(plugin_dir, ignore_errors=True)

    def test_discover_from_user_dir(self):
        """Discover a plugin from the user directory."""
        loader = PluginLoader()
        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp)
            loader._user_dir = user_dir
            plugin_dir = user_dir / "test-user"
            plugin_dir.mkdir()
            (plugin_dir / "manifest.json").write_text(json.dumps({
                "name": "test-user",
                "version": "2.0.0",
            }))

            manifests = loader._discover_from_dir(user_dir, "user")
            assert len(manifests) == 1
            assert manifests[0].name == "test-user"
            assert manifests[0].source == "user"

    def test_precedence_project_wins(self):
        """Project plugins override user and bundled with same name."""
        loader = PluginLoader()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            # Create project plugin
            project_dir = cwd / ".d2c" / "plugins" / "same-name"
            project_dir.mkdir(parents=True)
            (project_dir / "manifest.json").write_text(json.dumps({
                "name": "same-name",
                "version": "project-version",
            }))

            # Create user plugin with same name
            user_dir = Path(tmp) / "user-plugins"
            user_dir.mkdir(parents=True)
            user_plugin_dir = user_dir / "same-name"
            user_plugin_dir.mkdir()
            (user_plugin_dir / "manifest.json").write_text(json.dumps({
                "name": "same-name",
                "version": "user-version",
            }))

            loader._user_dir = user_dir
            manifests = loader.discover_all(cwd)

            same = [m for m in manifests if m.name == "same-name"]
            assert len(same) == 1
            assert same[0].version == "project-version"  # project wins
            assert same[0].source == "project"

    def test_empty_directories(self):
        """Empty plugin source directories → no errors."""
        loader = PluginLoader()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            (cwd / ".d2c" / "plugins").mkdir(parents=True)
            manifests = loader.discover_all(cwd)
            assert manifests == []

    def test_ignores_non_directories(self):
        """Files (not directories) in plugins dir are ignored."""
        loader = PluginLoader()
        with tempfile.TemporaryDirectory() as tmp:
            plugins_dir = Path(tmp)
            loader._bundled_dir = plugins_dir
            # A file, not a directory
            (plugins_dir / "README.md").write_text("not a plugin")

            manifests = loader._discover_from_dir(plugins_dir, "bundled")
            assert manifests == []

    def test_discover_and_load(self):
        """discover_and_load validates and returns LoadedPlugins."""
        loader = PluginLoader()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            project_plugins = cwd / ".d2c" / "plugins" / "load-test"
            project_plugins.mkdir(parents=True)
            (project_plugins / "manifest.json").write_text(json.dumps({
                "name": "load-test",
                "version": "1.0.0",
                "hooks": [
                    {"event": "SessionStart", "type": "command", "command": "echo start"},
                ],
                "skills": ["lint.md"],
            }))

            # Create the skill file
            (project_plugins / "lint.md").write_text(
                "---\ndescription: Lint on save\n---\n# Lint\nRun linter."
            )

            loaded = loader.discover_and_load(cwd)
            assert len(loaded) == 1
            assert loaded[0].is_valid
            assert loaded[0].manifest.name == "load-test"
            assert loaded[0].hooks_registered == 1
            assert loaded[0].skills_loaded == 1


# ── Hook Registration ─────────────────────────────────────────────────────

class TestPluginHookRegistration:
    def test_plugin_hooks_registered(self):
        """Plugin hooks are registered into HookRegistry."""
        loader = PluginLoader()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            project_plugins = cwd / ".d2c" / "plugins" / "hook-test"
            project_plugins.mkdir(parents=True)
            (project_plugins / "manifest.json").write_text(json.dumps({
                "name": "hook-test",
                "version": "1.0.0",
                "hooks": [
                    {"event": "PostToolUse", "type": "command", "command": "python hook.py"},
                    {"event": "PreToolUse", "type": "prompt", "prompt": "Check safety"},
                ],
            }))

            loaded = loader.discover_and_load(cwd)
            assert len(loaded) == 1

            # Register hooks into a HookRegistry
            registry = HookRegistry()
            for plugin in loaded:
                for hook_def in plugin.manifest.hooks:
                    definition = HookDefinition(
                        event=HookEvent(hook_def["event"]),
                        hook_type=HookType(hook_def.get("type", "command")),
                        command=hook_def.get("command"),
                        prompt=hook_def.get("prompt"),
                        source=f"plugin:{plugin.manifest.name}",
                    )
                    registry.register(definition)

            # Verify hooks were registered
            post_tool_hooks = registry._hooks[HookEvent.POST_TOOL_USE]
            pre_tool_hooks = registry._hooks[HookEvent.PRE_TOOL_USE]
            assert len(post_tool_hooks) == 1
            assert post_tool_hooks[0].source == "plugin:hook-test"
            assert len(pre_tool_hooks) == 1
            assert pre_tool_hooks[0].source == "plugin:hook-test"


# ── LoadedPlugin ──────────────────────────────────────────────────────────

class TestLoadedPlugin:
    def test_valid_plugin(self):
        manifest = PluginManifest(name="valid", version="1.0")
        plugin = LoadedPlugin(manifest=manifest, hooks_registered=2, skills_loaded=1)
        assert plugin.is_valid
        assert plugin.errors == []

    def test_invalid_plugin_with_errors(self):
        manifest = PluginManifest(name="invalid", version="1.0")
        plugin = LoadedPlugin(
            manifest=manifest,
            errors=["Dependency 'missing' not found"],
        )
        assert not plugin.is_valid
        assert len(plugin.errors) == 1


# ── Dependency Checking ───────────────────────────────────────────────────

class TestDependencies:
    def test_missing_dependency(self):
        """Plugin with missing dependency gets error."""
        loader = PluginLoader()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            project_plugins = cwd / ".d2c" / "plugins" / "needs-dep"
            project_plugins.mkdir(parents=True)
            (project_plugins / "manifest.json").write_text(json.dumps({
                "name": "needs-dep",
                "version": "1.0.0",
                "dependencies": ["nonexistent-plugin"],
            }))

            loaded = loader.discover_and_load(cwd)
            assert len(loaded) == 1
            assert not loaded[0].is_valid
            assert any("nonexistent-plugin" in e for e in loaded[0].errors)

    def test_dependency_resolved(self):
        """Plugin with resolved dependency loads successfully."""
        loader = PluginLoader()
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            plugins_dir = cwd / ".d2c" / "plugins"

            # Base plugin
            base_dir = plugins_dir / "base-plugin"
            base_dir.mkdir(parents=True)
            (base_dir / "manifest.json").write_text(json.dumps({
                "name": "base-plugin",
                "version": "1.0.0",
            }))

            # Plugin depending on base
            dep_dir = plugins_dir / "dependent-plugin"
            dep_dir.mkdir()
            (dep_dir / "manifest.json").write_text(json.dumps({
                "name": "dependent-plugin",
                "version": "1.0.0",
                "dependencies": ["base-plugin"],
            }))

            loaded = loader.discover_and_load(cwd)
            valid = [p for p in loaded if p.is_valid]
            assert len(valid) == 2


# ── Edge Cases ────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_plugin_directory_not_readable(self):
        """Non-existent base directory returns empty list."""
        loader = PluginLoader()
        manifests = loader._discover_from_dir(
            Path("/nonexistent/path/plugins"), "bundled",
        )
        assert manifests == []

    def test_manifest_with_unknown_fields(self):
        """Unknown fields are tolerated (logged but not rejected)."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            (plugin_dir / "manifest.json").write_text(json.dumps({
                "name": "future-plugin",
                "version": "1.0",
                "unknown_field": "should be fine",
                "another_unknown": 42,
            }))

            manifest = parse_manifest(plugin_dir)
            assert manifest is not None
            assert manifest.name == "future-plugin"

    def test_skills_list_with_non_strings(self):
        """Non-string skill entries are skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp)
            (plugin_dir / "manifest.json").write_text(json.dumps({
                "name": "mixed-skills",
                "version": "1.0",
                "skills": ["valid.md", 123, "also-valid.md"],
            }))

            manifest = parse_manifest(plugin_dir)
            assert manifest is not None
            assert manifest.skills == ["valid.md", "also-valid.md"]
