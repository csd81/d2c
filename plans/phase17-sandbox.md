# Phase 17: Shell Sandboxing

**Paper Reference:** Section 5 — `shouldUseSandbox.ts`, sandboxed command execution

**Priority:** MEDIUM

## Rationale

The paper mentions sandboxing as a key safety layer that "reduced the frequency of permission
prompts by an estimated 84%." It's a defense-in-depth measure. When sandboxing is active,
many commands that would normally require permission can run automatically because the
sandbox limits their blast radius.

## Files to Create/Modify

1. CREATE `src/d2c/sandbox.py` — sandbox configuration and detection
2. MODIFY `src/d2c/tools/bash_tool.py` — add sandboxed execution path

## Key Design

```python
@dataclass
class SandboxConfig:
    enabled: bool = False
    backend: str = "process"          # process | docker | windows-sandbox
    allowed_dirs: list[Path] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    network_enabled: bool = False
    max_memory_mb: int = 512
    timeout_ms: int = 120_000

class SandboxExecutor:
    def should_use_sandbox(self, command: str, config: SandboxConfig) -> bool:
        """Paper: shouldUseSandbox.ts — determines if sandboxing applies."""
        # Check if sandbox is enabled
        # Check if command is safe enough to NOT sandbox (ls, cat, etc.)
        # Check if command is too complex for sandbox

    async def execute_sandboxed(self, command: str, config: SandboxConfig) -> ToolResult:
        if config.backend == "process":
            return await self._process_sandbox(command, config)
        elif config.backend == "docker":
            return await self._docker_sandbox(command, config)

    async def _process_sandbox(self, command, config) -> ToolResult:
        """Windows job object / Unix cgroup + seccomp-based sandbox."""
        # On Windows: use Job Objects for resource limits
        # On Unix: use subprocess with restricted environment
        # Enforce: allowed dirs, no network, memory limits, timeout

    async def _docker_sandbox(self, command, config) -> ToolResult:
        """Docker-based sandbox (optional backend)."""
```

## Config

```yaml
# .d2c/config.yaml
sandbox:
  enabled: true
  backend: process
  allowed_dirs: ["."]
  network_enabled: false
```

## Edge Cases

- Sandbox not available on platform → warn, fall back to unsandboxed
- Sandbox process killed by OOM → error with memory limit message
- Network-enabled sandbox + sensitive command → ask for permission
- `dangerouslyDisableSandbox` flag → bypass sandbox (requires explicit permission)

## Tests (~10)

- should_use_sandbox returns True for arbitrary commands
- should_use_sandbox returns False for safe commands (ls, cat)
- Process sandbox restricts file access
- Sandbox timeout kills long-running commands
- Docker sandbox (when Docker available)
- dangerouslyDisableSandbox bypasses sandbox
- Sandbox unavailable → fallback with warning
- Network isolation in sandbox
