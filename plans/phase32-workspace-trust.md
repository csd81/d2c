# Phase 32: Workspace Trust Gate (Security Depth)

**Paper Reference:** Section 5.2, 11.3 — "adversarial pre-trust execution vulnerabilities... hooks, MCP server connections, and settings file resolution run before the interactive trust dialog... creating a structurally privileged phase where safety guarantees do not yet apply."

**Priority:** HIGH (Critical Security)

## Rationale

Currently, `d2c` eagerly loads project-level configuration, custom tools, and plugins from the active directory at startup. If a developer runs `d2c` in a cloned, untrusted open-source repository containing malicious `.d2c/plugins` or `.d2c/hooks`, arbitrary code can execute immediately.

To mitigate this, we will implement a Workspace Trust Gate. Before loading any local `.d2c` directory extensions, the system will verify whether the workspace path is trusted. If untrusted and custom local resources exist, the user must explicitly grant trust. Otherwise, the tool starts in a highly restricted mode.

---

## Files to Create/Modify

1. CREATE `src/d2c/permissions/trust.py` — workspace trust manager
2. MODIFY `src/d2c/main.py` — gate plugin loading and config resolution behind the trust check
3. CREATE `tests/test_workspace_trust.py` — verify trusted/untrusted paths behavior and restricted mode initialization

---

## Key Design

### 1. Workspace Trust Manager (`src/d2c/permissions/trust.py`)

A manager class that tracks trusted directory paths in `~/.d2c/trusted_directories.json`.

```python
import json
from pathlib import Path

class WorkspaceTrustManager:
    """Manages workspace trust mappings."""

    def __init__(self, trust_file: Path | None = None):
        self.trust_file = trust_file or Path.home() / ".d2c" / "trusted_directories.json"
        self._trusted_paths: set[str] = set()
        self._load()

    def _load(self) -> None:
        if self.trust_file.exists():
            try:
                data = json.loads(self.trust_file.read_text(encoding="utf-8"))
                self._trusted_paths = set(data.get("trusted_paths", []))
            except Exception:
                pass

    def _save(self) -> None:
        self.trust_file.parent.mkdir(parents=True, exist_ok=True)
        self.trust_file.write_text(
            json.dumps({"trusted_paths": list(self._trusted_paths)}, indent=2),
            encoding="utf-8"
        )

    def is_trusted(self, path: Path) -> bool:
        """Check if path or any of its parents are explicitly trusted."""
        resolved = str(path.resolve())
        for tp in self._trusted_paths:
            if resolved.startswith(tp):
                return True
        return False

    def grant_trust(self, path: Path) -> None:
        """Add path to trusted directories list."""
        self._trusted_paths.add(str(path.resolve()))
        self._save()
```

### 2. Gating Eager Loading in `main.py`

Modify `run_headless` and `run_interactive` to perform the trust check before loading configurations or plugins:

```python
async def check_workspace_trust(cwd: Path) -> bool:
    """Verify trust. Prompts user if untrusted and local extensions exist."""
    # Check if local extensions are present
    has_local_extensions = (
        (cwd / ".d2c" / "plugins").is_dir() or
        (cwd / ".d2c" / "agents").is_dir() or
        (cwd / ".d2c" / "skills").is_dir() or
        (cwd / ".d2c" / "config.yaml").exists()
    )
    
    if not has_local_extensions:
        return True # Safe to run, no local extensions to exploit

    trust_manager = WorkspaceTrustManager()
    if trust_manager.is_trusted(cwd):
        return True

    # Prompt user
    print(f"\n[WARNING] d2c detected custom plugins/rules in this workspace: {cwd}")
    print("Trusting this workspace allows it to run local plugins and git hooks.")
    
    choice = input("Do you trust this directory? (y/N): ").strip().lower()
    if choice in ("y", "yes"):
        trust_manager.grant_trust(cwd)
        return True
    return False
```

### 3. Enforcing Restricted Mode
If the workspace is untrusted (the user selected `N` or aborted):
1. **Ignore local extensions**: Skip calling `_load_plugins` and do not search `.d2c/plugins/` or `.d2c/agents/`.
2. **Standard tools only**: Only load global system tools. Disallow custom plugins.
3. **Escalate Shell Permissions**: Force `permission_mode` to `plan` or `default` (blocking automated shell executions even if `.d2c/config.yaml` claims `dontAsk` or `acceptEdits`).
4. **Clean environment variables**: Strip local `.env` configuration files to prevent token extraction/hijacking.

---

## Edge Cases

* **Non-Interactive Environments (headless `-p` or CI runs)**: If `d2c` is called headlessly (e.g. `d2c "fix bug"`) in an untrusted directory with custom extensions, the script must abort immediately with a trust warning rather than blocking on stdin.
* **Symbolic Links**: Malicious repositories might link to system directories. Paths must be `.resolve()`d before checking trust status.

---

## Tests

Verify the following:
* `test_untrusted_folder_prevents_plugin_loading`: Verifies that if trust is denied, local plugins are not registered.
* `test_trusted_folder_auto_allows_plugins`: Verifies that if folder is in `trusted_directories.json`, loading proceeds without prompting.
* `test_headless_abort_on_untrusted_directory`: Verifies that running in non-interactive mode in an untrusted folder exits with status 1.
* `test_untrusted_forces_restricted_permission_mode`: Verifies permission mode falls back to safe defaults (e.g. `default` instead of `dontAsk`).
