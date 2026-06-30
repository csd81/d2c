# Phase 13: Plugin System

**Paper Reference:** Section 6 — one of 4 extensibility mechanisms

**Priority:** HIGH

## Rationale

Plugins contribute hooks, skills, commands, and subagent definitions. They are loaded
at session start and provide zero-code extension points. The paper states: "Hook sources
include settings.json, plugins, and managed policy at startup; skill hooks register
dynamically on invocation."

## Files to Create/Modify

1. CREATE `src/d2c/plugins/__init__.py`
2. CREATE `src/d2c/plugins/loader.py` — plugin discovery and loading
3. CREATE `src/d2c/plugins/manifest.py` — plugin manifest schema
4. MODIFY `src/d2c/main.py` — load plugins at startup
5. MODIFY `src/d2c/hooks.py` — register plugin hooks

## Key Design

```python
@dataclass
class PluginManifest:
    name: str
    version: str
    description: str = ""
    hooks: list[dict] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    mcp_servers: list[dict] = field(default_factory=list)

class PluginLoader:
    """Discovers and loads plugins from multiple sources."""
    def __init__(self):
        self.sources = [
            BundledPluginsSource(),          # d2c's built-in plugins
            UserPluginsSource(),             # ~/.d2c/plugins/
            ProjectPluginsSource(),          # .d2c/plugins/
        ]
    def discover(self) -> list[PluginManifest]: ...
    def load(self, manifest: PluginManifest) -> LoadedPlugin: ...
```

**Plugin sources (paper: "Hook sources include settings.json, plugins, and managed policy"):**
1. Bundled: `src/d2c/plugins/bundled/` — ship with d2c
2. User: `~/.d2c/plugins/` — per-user plugins
3. Project: `.d2c/plugins/` — per-project plugins (highest precedence)

**A plugin is a directory with `manifest.json`:**
```json
{
  "name": "lint-on-save",
  "version": "1.0.0",
  "description": "Runs linter after every file write",
  "hooks": [
    {
      "event": "PostToolUse",
      "type": "command",
      "command": "python .d2c/plugins/lint-on-save/hook.py"
    }
  ],
  "skills": ["commit"],
  "agents": ["code-reviewer"]
}
```

## Integration

At startup in `main.py`, after config loading:
```python
loader = PluginLoader()
registry = loader.discover_and_load()
for manifest, plugin in registry:
    for hook_def in manifest.hooks:
        hook_registry.register(HookDefinition(...))
    for skill_path in manifest.skills:
        skill_tool.register(load_skill(skill_path))
    for agent_path in manifest.agents:
        agent_registry.register(load_agent(agent_path))
```

## Edge Cases

- Plugin fails to load → warn, continue without it
- Plugin with same name from multiple sources → project wins
- Plugin depends on another plugin → dependency ordering
- Malformed manifest → skip with error message

## Tests (~10)

- Plugin discovery from bundled directory
- Plugin discovery from user directory
- Plugin precedence: project > user > bundled
- Plugin hooks registered into HookRegistry
- Plugin skills added to SkillTool
- Malformed manifest produces warning, not crash
- Plugin with missing dependency → skip with error
- Plugin hot-reload not supported (load once at startup)
