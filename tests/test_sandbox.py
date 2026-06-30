"""Tests for Phase 17: Shell Sandboxing.

Covers: SandboxConfig, SandboxExecutor.should_use_sandbox,
process sandbox execution, BashTool integration, edge cases.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from d2c.sandbox import (
    SandboxConfig, SandboxExecutor, SandboxResult,
    SAFE_READONLY_COMMANDS,
)
from d2c.tools.bash_tool import BashTool
from d2c.tools import ToolResult


# ── SandboxConfig tests ──────────────────────────────────────────────────

class TestSandboxConfig:
    def test_default_config_disabled(self):
        config = SandboxConfig()
        assert config.enabled is False
        assert config.backend == "process"
        assert config.network_enabled is False
        assert config.max_memory_mb == 512
        assert config.timeout_ms == 120_000

    def test_from_dict_enabled(self):
        config = SandboxConfig.from_dict({
            "enabled": True,
            "backend": "process",
            "allowed_dirs": ["/tmp", "/home"],
            "allowed_commands": ["git", "npm"],
            "network_enabled": True,
            "max_memory_mb": 256,
            "timeout_ms": 60_000,
        })
        assert config.enabled is True
        assert config.backend == "process"
        assert len(config.allowed_dirs) == 2
        assert config.allowed_commands == ["git", "npm"]
        assert config.network_enabled is True
        assert config.max_memory_mb == 256
        assert config.timeout_ms == 60_000

    def test_from_dict_empty(self):
        config = SandboxConfig.from_dict(None)
        assert config.enabled is False

    def test_from_dict_defaults(self):
        config = SandboxConfig.from_dict({})
        assert config.enabled is False


# ── SandboxExecutor.should_use_sandbox tests ─────────────────────────────

class TestShouldUseSandbox:
    """Paper: shouldUseSandbox.ts logic."""

    def test_disabled_returns_false(self):
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=False)
        assert executor.should_use_sandbox("rm -rf /", config) is False
        assert executor.should_use_sandbox("curl evil.com", config) is False

    def test_empty_command_returns_false(self):
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)
        assert executor.should_use_sandbox("", config) is False
        assert executor.should_use_sandbox("   ", config) is False

    def test_safe_readonly_commands_skip_sandbox(self):
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)
        for cmd in ("ls -la", "cat file.txt", "echo hello", "pwd",
                     "head -n 10 file", "tail file", "wc -l file",
                     "sort file", "uniq file", "which python",
                     "whoami", "hostname", "date", "grep pattern file"):
            assert executor.should_use_sandbox(cmd, config) is False, \
                f"'{cmd}' should skip sandbox (safe read-only)"

    def test_arbitrary_commands_use_sandbox(self):
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)
        for cmd in ("rm -rf /tmp/foo", "curl https://example.com",
                     "pip install requests", "kubectl delete pod",
                     "docker rm -f container", "chmod 644 file.txt"):
            assert executor.should_use_sandbox(cmd, config) is True, \
                f"'{cmd}' should use sandbox"

    def test_dangerously_disable_sandbox_flag(self):
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)
        assert executor.should_use_sandbox(
            "rm -rf /tmp/foo --dangerously-disable-sandbox", config,
        ) is False

    def test_git_commands_skip_sandbox(self):
        """Git is in the safe list for developer workflows."""
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)
        assert executor.should_use_sandbox("git status", config) is False
        assert executor.should_use_sandbox("git log --oneline", config) is False

    def test_package_managers_skip_sandbox(self):
        """npm, npx, cargo, go are in safe list."""
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)
        assert executor.should_use_sandbox("npm test", config) is False
        assert executor.should_use_sandbox("cargo build", config) is False
        assert executor.should_use_sandbox("go build ./...", config) is False


# ── SandboxExecutor.is_dangerous tests ───────────────────────────────────

class TestIsDangerous:
    def test_dangerous_commands(self):
        executor = SandboxExecutor()
        for cmd in ("sudo rm -rf /", "su -", "passwd", "chown root:root file",
                     "mount /dev/sda1 /mnt", "umount /mnt"):
            assert executor.is_dangerous(cmd) is True, \
                f"'{cmd}' should be dangerous"

    def test_safe_commands_not_dangerous(self):
        executor = SandboxExecutor()
        for cmd in ("ls", "cat file", "echo hello", "git status", "npm install"):
            assert executor.is_dangerous(cmd) is False


# ── Process sandbox execution tests ──────────────────────────────────────

class TestProcessSandbox:
    def test_sandboxed_echo(self):
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)

        async def run():
            result = await executor.execute_sandboxed(
                "echo hello-world-123", config,
            )
            return result

        result = asyncio.run(run())
        assert result.sandboxed is True
        assert "hello-world-123" in result.output
        assert result.error is False
        assert result.timed_out is False

    def test_sandboxed_command_error(self):
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)

        async def run():
            result = await executor.execute_sandboxed(
                "nonexistent-command-xyz", config,
            )
            return result

        result = asyncio.run(run())
        assert result.sandboxed is True
        # Should error since command doesn't exist
        assert result.error is True

    def test_sandbox_timeout(self):
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)

        async def run():
            result = await executor.execute_sandboxed(
                "Start-Sleep -Seconds 30",  # PowerShell sleep
                config,
                timeout_ms=500,  # Very short timeout
            )
            return result

        result = asyncio.run(run())
        assert result.timed_out is True
        assert result.error is True

    @pytest.mark.parametrize("backend", ["process"])
    def test_sandboxed_command_returns_exit_code(self, backend):
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True, backend=backend)

        async def run():
            result = await executor.execute_sandboxed(
                "Write-Output success", config,
            )
            return result

        result = asyncio.run(run())
        assert result.exit_code == 0

    def test_sandbox_result_dataclass(self):
        result = SandboxResult(output="test", exit_code=0, sandboxed=True, backend="docker")
        assert result.output == "test"
        assert result.sandboxed is True
        assert result.backend == "docker"
        assert result.timed_out is False

    def test_sandbox_result_timed_out(self):
        result = SandboxResult(output="timeout", exit_code=-1, error=True, timed_out=True)
        assert result.timed_out is True
        assert result.error is True


# ── BashTool sandbox integration tests ───────────────────────────────────

class TestBashToolSandbox:
    def test_bash_tool_without_sandbox(self):
        """BashTool without sandbox config runs normally."""
        tool = BashTool()

        async def run():
            result = await tool.execute("echo normal-execution")
            return result

        result = asyncio.run(run())
        assert isinstance(result, ToolResult)
        assert "normal-execution" in result.output

    def test_bash_tool_with_sandbox_safe_command(self):
        """Safe command bypasses sandbox even when enabled."""
        tool = BashTool(sandbox_config=SandboxConfig(enabled=True))

        async def run():
            result = await tool.execute("echo safe-command")
            return result

        result = asyncio.run(run())
        assert isinstance(result, ToolResult)
        # 'echo' is safe → should not be sandboxed
        assert result.metadata.get("sandboxed") is not True

    def test_bash_tool_with_sandbox_arbitrary_command(self):
        """Arbitrary command runs through sandbox when enabled."""
        tool = BashTool(sandbox_config=SandboxConfig(enabled=True))

        async def run():
            # 'Write-Output' is NOT in SAFE_READONLY_COMMANDS
            result = await tool.execute("Write-Output sandboxed-output")
            return result

        result = asyncio.run(run())
        assert isinstance(result, ToolResult)
        assert "sandboxed-output" in result.output
        # Should be sandboxed
        assert result.metadata.get("sandboxed") is True

    def test_bash_tool_dangerously_disable_sandbox(self):
        """dangerouslyDisableSandbox flag bypasses sandbox."""
        tool = BashTool(sandbox_config=SandboxConfig(enabled=True))

        async def run():
            result = await tool.execute(
                "Write-Output no-sandbox",
                dangerouslyDisableSandbox=True,
            )
            return result

        result = asyncio.run(run())
        assert isinstance(result, ToolResult)
        assert "no-sandbox" in result.output
        assert result.metadata.get("sandboxed") is not True

    def test_bash_tool_sandbox_disabled(self):
        """When sandbox is disabled, all commands run normally."""
        tool = BashTool(sandbox_config=SandboxConfig(enabled=False))

        async def run():
            result = await tool.execute("Write-Output normal-exec")
            return result

        result = asyncio.run(run())
        assert isinstance(result, ToolResult)
        assert result.metadata.get("sandboxed") is not True


# ── Edge cases ────────────────────────────────────────────────────────────

class TestSandboxEdgeCases:
    def test_command_with_path_still_checks_basename(self):
        """'/usr/bin/ls' should be recognized as safe (basename 'ls')."""
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)
        assert executor.should_use_sandbox("/usr/bin/ls -la", config) is False

    def test_find_command_skips_sandbox(self):
        """find is in the safe list."""
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True)
        assert executor.should_use_sandbox("find . -name '*.py'", config) is False

    def test_docker_backend_unavailable_fallback(self):
        """Docker backend when Docker not installed should not crash."""
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True, backend="docker")

        async def run():
            result = await executor.execute_sandboxed(
                "echo test", config, timeout_ms=5000,
            )
            return result

        result = asyncio.run(run())
        # Should either work (if Docker is installed) or return an error
        assert isinstance(result, SandboxResult)

    def test_windows_sandbox_falls_back(self):
        """Windows Sandbox backend falls back to process sandbox."""
        executor = SandboxExecutor()
        config = SandboxConfig(enabled=True, backend="windows-sandbox")

        async def run():
            result = await executor.execute_sandboxed(
                "echo fallback-test", config,
            )
            return result

        result = asyncio.run(run())
        assert isinstance(result, SandboxResult)
        assert result.sandboxed is True

    def test_safe_readonly_commands_set_contains_expected(self):
        """Verify known-safe commands are in the set."""
        for cmd in ("ls", "cat", "echo", "pwd", "head", "tail", "wc",
                     "grep", "find", "git", "npm", "cargo", "go"):
            assert cmd in SAFE_READONLY_COMMANDS, \
                f"'{cmd}' should be in SAFE_READONLY_COMMANDS"

    def test_dangerous_commands_set_contains_expected(self):
        """Verify dangerous commands are in the set."""
        for cmd in ("sudo", "su", "passwd", "chown", "mount", "umount"):
            assert cmd in SandboxExecutor.DANGEROUS_COMMANDS
