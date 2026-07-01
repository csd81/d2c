"""Execute shell commands.

Supports Bash on Unix, PowerShell on Windows. Commands run in the project
working directory. Long-running commands can be backgrounded.
"""

from __future__ import annotations

import asyncio
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult

if TYPE_CHECKING:
    from d2c.sandbox import SandboxConfig, SandboxExecutor


class BashTool(Tool):
    name: ClassVar[str] = "Bash"
    description: ClassVar[str] = (
        "Executes a given bash command and returns its output. "
        "The working directory persists between commands, but shell state does not. "
        "Use forward slashes in paths on all platforms. "
        "Avoid interactive commands that require user input."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in milliseconds (max 600000, default 120000).",
            },
            "description": {
                "type": "string",
                "description": "Clear, concise description of what this command does.",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run the command in the background.",
            },
            "dangerouslyDisableSandbox": {
                "type": "boolean",
                "description": "Set to true to dangerously override sandbox mode and run commands without sandboxing.",
            },
        },
        "required": ["command"],
    }
    category: ClassVar[PermissionCategory] = PermissionCategory.SHELL
    is_concurrent_safe: ClassVar[bool] = False

    def __init__(
        self,
        cwd: Path | None = None,
        sandbox_config: "SandboxConfig | None" = None,
    ):
        self._cwd = cwd or Path.cwd()
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._sandbox_config = sandbox_config
        self._sandbox_executor = None

    def _get_sandbox_executor(self) -> "SandboxExecutor":
        """Lazy init sandbox executor."""
        if self._sandbox_executor is None:
            from d2c.sandbox import SandboxExecutor

            self._sandbox_executor = SandboxExecutor()
        return self._sandbox_executor

    async def execute(
        self,
        command: str,
        timeout: int = 120_000,
        description: str = "",
        run_in_background: bool = False,
        dangerouslyDisableSandbox: bool = False,
    ) -> ToolResult:
        # Phase 17: Sandbox check
        sandbox_config = self._sandbox_config
        if sandbox_config and sandbox_config.enabled:
            executor = self._get_sandbox_executor()

            # Skip sandbox if dangerouslyDisableSandbox is set
            if not dangerouslyDisableSandbox and executor.should_use_sandbox(
                command,
                sandbox_config,
            ):
                return await self._execute_sandboxed(
                    command,
                    timeout,
                    sandbox_config,
                )

        if platform.system() == "Windows":
            return await self._execute_windows(command, timeout, run_in_background)
        else:
            return await self._execute_unix(command, timeout, run_in_background)

    async def _execute_sandboxed(
        self,
        command: str,
        timeout: int,
        config: "SandboxConfig",
    ) -> ToolResult:
        """Execute a command through the sandbox."""
        from d2c.sandbox import SandboxResult

        result: SandboxResult = await self._get_sandbox_executor().execute_sandboxed(
            command=command,
            config=config,
            cwd=self._cwd,
            timeout_ms=timeout,
        )

        return ToolResult(
            output=result.output,
            error=result.error,
            metadata={
                "exit_code": result.exit_code,
                "command": command,
                "sandboxed": result.sandboxed,
                "timed_out": result.timed_out,
                "sandbox_backend": result.backend,
            },
        )

    async def _execute_unix(
        self,
        command: str,
        timeout_ms: int,
        run_in_background: bool,
    ) -> ToolResult:
        timeout_sec = min(timeout_ms, 600_000) / 1000.0

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._cwd),
            )
        except OSError as e:
            return ToolResult(output=f"Error creating process: {e}", error=True)

        if run_in_background:
            task_id = f"bg_{id(proc)}"
            self._background_tasks[task_id] = asyncio.create_task(
                self._collect_background(proc, task_id)
            )
            return ToolResult(
                output=f"Command started in background: {command}",
                metadata={"background_task_id": task_id},
            )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()  # wait for process to fully terminate
            return ToolResult(
                output=f"Error: command timed out after {timeout_ms}ms.\nCommand: {command}",
                error=True,
                metadata={"exit_code": -1, "timed_out": True},
            )

        return self._build_result(
            command,
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    async def _execute_windows(
        self,
        command: str,
        timeout_ms: int,
        run_in_background: bool,
    ) -> ToolResult:
        timeout_sec = min(timeout_ms, 600_000) / 1000.0

        try:
            proc = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._cwd),
            )
        except OSError as e:
            return ToolResult(output=f"Error creating process: {e}", error=True)

        if run_in_background:
            task_id = f"bg_{id(proc)}"
            self._background_tasks[task_id] = asyncio.create_task(
                self._collect_background(proc, task_id)
            )
            return ToolResult(
                output=f"Command started in background: {command}",
                metadata={"background_task_id": task_id},
            )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()  # wait for process to fully terminate (Windows cleanup)
            return ToolResult(
                output=f"Error: command timed out after {timeout_ms}ms.\nCommand: {command}",
                error=True,
                metadata={"exit_code": -1, "timed_out": True},
            )

        return self._build_result(
            command,
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    async def _collect_background(self, proc: asyncio.subprocess.Process, task_id: str) -> None:
        try:
            await proc.wait()
        finally:
            self._background_tasks.pop(task_id, None)

    @staticmethod
    def _build_result(
        command: str,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> ToolResult:
        output_parts = []
        if stdout:
            output_parts.append(stdout.strip())
        if stderr:
            output_parts.append(f"[stderr]\n{stderr.strip()}")

        output = "\n".join(output_parts) if output_parts else "(no output)"

        return ToolResult(
            output=output,
            error=exit_code != 0,
            metadata={
                "exit_code": exit_code,
                "command": command,
                "stdout_length": len(stdout),
                "stderr_length": len(stderr),
            },
        )
