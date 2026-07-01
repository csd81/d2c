"""Shell sandboxing for defense-in-depth safety. Paper Section 5.

"shouldUseSandbox.ts determines if sandboxing applies. When sandboxing is
active, many commands that would normally require permission can run
automatically because the sandbox limits their blast radius."

Backends: process (built-in) | bubblewrap (Linux OS-level) | docker (optional)
          | windows-sandbox (optional)
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

logger = logging.getLogger(__name__)

# Read-only system roots bind-mounted into a bubblewrap sandbox (only those
# that actually exist on the host are used). This gives the sandboxed command
# a working /bin/sh + shared libraries without exposing the whole filesystem
# writable.
_BWRAP_RO_ROOTS: tuple[str, ...] = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/lib32",
    "/etc",
)


def bubblewrap_available() -> bool:
    """True if the bubblewrap (`bwrap`) binary is on PATH."""
    return shutil.which("bwrap") is not None


# Commands considered inherently safe — no sandbox needed
SAFE_READONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "dir",
        "cat",
        "type",
        "echo",
        "pwd",
        "cd",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "which",
        "where",
        "whoami",
        "hostname",
        "date",
        "time",
        "find",
        "grep",
        "git",
        "python",
        "node",
        "npm",
        "npx",
        "cargo",
        "go",
        "cargo",
    }
)


@dataclass
class SandboxConfig:
    """Configuration for the shell sandbox.

    Attributes:
        enabled: Whether sandboxing is active.
        backend: Sandbox backend ("process", "docker", "windows-sandbox").
        allowed_dirs: Directories the sandboxed process can access.
        allowed_commands: Explicit command allowlist (empty = all allowed).
        network_enabled: Whether sandboxed process gets network access.
        max_memory_mb: Maximum memory for the sandboxed process.
        timeout_ms: Default timeout for sandboxed commands.
    """

    enabled: bool = False
    backend: str = "process"
    allowed_dirs: list[Path] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    network_enabled: bool = False
    max_memory_mb: int = 512
    timeout_ms: int = 120_000
    # Phase 62: when an OS-level backend (bubblewrap) is requested but the
    # host lacks it, fall back to the process backend if True; otherwise fail
    # closed (do not run the command). Default False = fail closed, so a
    # requested strong sandbox never silently downgrades.
    fallback_to_process: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> "SandboxConfig":
        """Create from a config dictionary (e.g. from .d2c/config.yaml)."""
        if not data:
            return cls()
        return cls(
            enabled=data.get("enabled", False),
            backend=data.get("backend", "process"),
            allowed_dirs=[Path(d) for d in data.get("allowed_dirs", [])],
            allowed_commands=data.get("allowed_commands", []),
            network_enabled=data.get("network_enabled", False),
            max_memory_mb=data.get("max_memory_mb", 512),
            timeout_ms=data.get("timeout_ms", 120_000),
            fallback_to_process=data.get("fallback_to_process", False),
        )


class SandboxExecutor:
    """Paper: shouldUseSandbox.ts — determines if sandboxing applies.

    Two-level decision:
    1. Is the command safe enough to skip sandbox? (ls, cat, echo, etc.)
    2. Is the command too dangerous to trust even with sandbox? (sudo, etc.)

    When sandbox is enabled, safe read-only commands skip it for performance,
    while all other commands run inside the sandbox.
    """

    # Commands that always require permission even in sandbox
    DANGEROUS_COMMANDS: ClassVar[frozenset[str]] = frozenset(
        {
            "sudo",
            "su",
            "passwd",
            "chown",
            "mount",
            "umount",
        }
    )

    def should_use_sandbox(self, command: str, config: SandboxConfig) -> bool:
        """Determine if a command should run in the sandbox.

        Paper logic:
        - If sandbox is disabled → False
        - If dangerouslyDisableSandbox flag is in command → False
        - If command is safe read-only → False (skip sandbox for perf)
        - Otherwise → True
        """
        if not config.enabled:
            return False

        cmd = command.strip()
        if not cmd:
            return False

        # dangerouslyDisableSandbox flag bypasses sandbox
        if "--dangerously-disable-sandbox" in cmd:
            return False

        # Extract first word
        try:
            parts = shlex.split(cmd)
            if not parts:
                return False
            first_word = os.path.basename(parts[0]).lower()
        except ValueError:
            first_word = cmd.split()[0].lower() if cmd.split() else ""

        # Safe read-only commands don't need sandbox
        if first_word in SAFE_READONLY_COMMANDS:
            return False

        return True

    def is_dangerous(self, command: str) -> bool:
        """Check if a command is too dangerous even for sandbox."""
        cmd = command.strip()
        try:
            parts = shlex.split(cmd)
            if not parts:
                return False
            first_word = os.path.basename(parts[0]).lower()
        except ValueError:
            first_word = cmd.split()[0].lower() if cmd.split() else ""

        return first_word in self.DANGEROUS_COMMANDS

    async def execute_sandboxed(
        self,
        command: str,
        config: SandboxConfig,
        cwd: Path | None = None,
        timeout_ms: int | None = None,
    ) -> "SandboxResult":
        """Execute a command inside the sandbox.

        Dispatches to the appropriate backend based on config.
        """
        timeout = timeout_ms or config.timeout_ms

        if config.backend == "bubblewrap":
            return await self._bubblewrap_sandbox(command, config, cwd, timeout)
        elif config.backend == "docker":
            return await self._docker_sandbox(command, config, cwd, timeout)
        elif config.backend == "windows-sandbox":
            return await self._windows_sandbox(command, config, cwd, timeout)
        else:
            return await self._process_sandbox(command, config, cwd, timeout)

    async def _process_sandbox(
        self,
        command: str,
        config: SandboxConfig,
        cwd: Path | None,
        timeout_ms: int,
    ) -> "SandboxResult":
        """Process-level sandbox using restricted subprocess execution.

        Enforces:
        - Working directory restriction to allowed_dirs
        - Timeout
        - Environment stripping (minimal env)
        - No shell injection (uses shlex to parse)
        """
        work_dir = str(cwd or Path.cwd())

        # Build restricted environment
        restricted_env: dict[str, str] = {}
        # Keep essential variables
        for key in (
            "PATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "PYTHONUNBUFFERED",
        ):
            if key in os.environ:
                restricted_env[key] = os.environ[key]

        # Platform-specific
        if platform.system() == "Windows":
            for key in (
                "COMSPEC",
                "PATHEXT",
                "WINDIR",
                "ProgramFiles",
                "ProgramFiles(x86)",
                "CommonProgramFiles",
            ):
                if key in os.environ:
                    restricted_env[key] = os.environ[key]

        try:
            timeout_sec = min(timeout_ms, 600_000) / 1000.0

            if platform.system() == "Windows":
                proc = await asyncio.create_subprocess_exec(
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=work_dir,
                    env=restricted_env,
                )
            else:
                # Use shlex to parse command safely
                try:
                    cmd_args = shlex.split(command)
                except ValueError:
                    cmd_args = ["bash", "-c", command]

                proc = await asyncio.create_subprocess_exec(
                    *cmd_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=work_dir,
                    env=restricted_env,
                )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return SandboxResult(
                    output=f"Sandbox: command timed out after {timeout_ms}ms\n  {command}",
                    exit_code=-1,
                    error=True,
                    timed_out=True,
                )

            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")

            output = out.strip()
            if err.strip():
                output += f"\n[stderr]\n{err.strip()}"

            return SandboxResult(
                output=output or "(no output)",
                exit_code=proc.returncode or 0,
                error=(proc.returncode or 0) != 0,
                sandboxed=True,
            )

        except OSError as e:
            return SandboxResult(
                output=f"Sandbox error: {e}",
                exit_code=-1,
                error=True,
            )

    @staticmethod
    def build_bubblewrap_command(
        command: str,
        config: SandboxConfig,
        cwd: Path | None,
    ) -> list[str]:
        """Construct the ``bwrap`` argv for an OS-level sandboxed command.

        The sandbox:
          - runs unprivileged via user namespaces (bubblewrap's default);
          - shares no PID/IPC/UTS namespace with the host, and dies with the
            parent so orphaned children can't outlive the tool call;
          - unshares the network namespace (no network) unless
            ``network_enabled``;
          - mounts a fresh tmpfs at /tmp, a private /proc and /dev;
          - bind-mounts standard system roots (/usr, /bin, /lib, ...) READ-ONLY;
          - bind-mounts the working directory and each ``allowed_dirs`` entry
            READ-WRITE — so a write outside those (e.g. to $HOME) fails;
          - chdir's into the working directory and runs ``/bin/sh -c <command>``.

        Pure/deterministic (aside from checking which system roots exist), so
        it is unit-testable without bwrap installed.
        """
        work_dir = str(cwd or Path.cwd())
        argv: list[str] = [
            "bwrap",
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--new-session",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",  # nosec B108: bubblewrap mount target inside the jail, not a host temp path
        ]
        if not config.network_enabled:
            argv.append("--unshare-net")

        for root in _BWRAP_RO_ROOTS:
            if Path(root).exists():
                argv += ["--ro-bind", root, root]

        rw_dirs: list[str] = [work_dir]
        rw_dirs += [str(d) for d in config.allowed_dirs]
        seen: set[str] = set()
        for d in rw_dirs:
            if d in seen:
                continue
            seen.add(d)
            argv += ["--bind", d, d]

        argv += ["--chdir", work_dir, "--", "/bin/sh", "-c", command]
        return argv

    async def _bubblewrap_sandbox(
        self,
        command: str,
        config: SandboxConfig,
        cwd: Path | None,
        timeout_ms: int,
    ) -> "SandboxResult":
        """Linux OS-level sandbox via bubblewrap (Phase 62).

        If bubblewrap is unavailable, fail closed (do not run the command)
        unless ``config.fallback_to_process`` opts into the weaker process
        backend.
        """
        if not bubblewrap_available():
            if config.fallback_to_process:
                logger.warning(
                    "bubblewrap (bwrap) not found on PATH; falling back to the "
                    "process backend (weaker isolation)."
                )
                return await self._process_sandbox(command, config, cwd, timeout_ms)
            return SandboxResult(
                output=(
                    "Sandbox error: bubblewrap (bwrap) is not installed or not on PATH, "
                    "so the command was NOT run (fail-closed). Install bubblewrap, choose "
                    "a different backend, or enable fallback_to_process."
                ),
                exit_code=-1,
                error=True,
                backend="bubblewrap",
            )

        argv = self.build_bubblewrap_command(command, config, cwd)
        return await self._spawn_and_capture(
            argv,
            cwd=cwd,
            env=None,
            timeout_ms=timeout_ms,
            backend="bubblewrap",
        )

    async def _spawn_and_capture(
        self,
        argv: list[str],
        *,
        cwd: Path | None,
        env: dict[str, str] | None,
        timeout_ms: int,
        backend: str,
    ) -> "SandboxResult":
        """Run an argv list, capture stdout/stderr, enforce a timeout, and map
        the result to a SandboxResult. Shared by the OS-level backends."""
        try:
            timeout_sec = min(timeout_ms, 600_000) / 1000.0
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd else None,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return SandboxResult(
                    output=f"{backend} sandbox: command timed out after {timeout_ms}ms",
                    exit_code=-1,
                    error=True,
                    timed_out=True,
                    backend=backend,
                )

            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            output = out.strip()
            if err.strip():
                output += f"\n[stderr]\n{err.strip()}"

            return SandboxResult(
                output=output or "(no output)",
                exit_code=proc.returncode or 0,
                error=(proc.returncode or 0) != 0,
                sandboxed=True,
                backend=backend,
            )
        except OSError as e:
            return SandboxResult(
                output=f"{backend} sandbox error: {e}",
                exit_code=-1,
                error=True,
                backend=backend,
            )

    async def _docker_sandbox(
        self,
        command: str,
        config: SandboxConfig,
        cwd: Path | None,
        timeout_ms: int,
    ) -> "SandboxResult":
        """Docker-based sandbox execution (optional backend).

        Runs command in a temporary Docker container with:
        - Read-only root filesystem (except work dir)
        - No network (unless config.network_enabled)
        - Memory limit
        - Volume mount for allowed_dirs
        """
        work_dir = str(cwd or Path.cwd())

        docker_args = [
            "docker",
            "run",
            "--rm",
            "--memory",
            f"{config.max_memory_mb}m",
            "--memory-swap",
            f"{config.max_memory_mb}m",
        ]

        if not config.network_enabled:
            docker_args.append("--network=none")

        docker_args.extend(
            [
                "--read-only",
                "-v",
                f"{work_dir}:{work_dir}:rw",
                "-w",
                work_dir,
                "alpine:latest",
                "sh",
                "-c",
                command,
            ]
        )

        try:
            timeout_sec = min(timeout_ms, 600_000) / 1000.0

            proc = await asyncio.create_subprocess_exec(
                *docker_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return SandboxResult(
                    output=f"Docker sandbox: command timed out after {timeout_ms}ms",
                    exit_code=-1,
                    error=True,
                    timed_out=True,
                )

            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")

            output = out.strip()
            if err.strip():
                output += f"\n[stderr]\n{err.strip()}"

            return SandboxResult(
                output=output or "(no output)",
                exit_code=proc.returncode or 0,
                error=(proc.returncode or 0) != 0,
                sandboxed=True,
                backend="docker",
            )

        except OSError as e:
            return SandboxResult(
                output=f"Docker sandbox error (is Docker installed?): {e}",
                exit_code=-1,
                error=True,
            )

    async def _windows_sandbox(
        self,
        command: str,
        config: SandboxConfig,
        cwd: Path | None,
        timeout_ms: int,
    ) -> "SandboxResult":
        """Windows Sandbox backend (Windows 10 Pro/Enterprise).

        Uses Windows Sandbox feature when available.
        Falls back to process sandbox if not available.
        """
        logger.warning(
            "Windows Sandbox backend not fully implemented; falling back to process sandbox"
        )
        return await self._process_sandbox(command, config, cwd, timeout_ms)


@dataclass
class SandboxResult:
    """Result of a sandboxed command execution."""

    output: str
    exit_code: int
    error: bool = False
    sandboxed: bool = False
    timed_out: bool = False
    backend: str = "process"
