"""Phase 62: OS-level (bubblewrap) sandbox backend.

Command-construction and unavailable-handling tests are deterministic and
always run. Confinement tests actually execute bubblewrap and are skipped
when `bwrap` is not installed.
"""

from __future__ import annotations

import asyncio
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from d2c.sandbox import (
    SandboxConfig,
    SandboxExecutor,
    bubblewrap_available,
)

_HAS_BWRAP = bubblewrap_available()
_IS_LINUX = platform.system() == "Linux"

requires_bwrap = pytest.mark.skipif(
    not (_HAS_BWRAP and _IS_LINUX), reason="bubblewrap (bwrap) not available on this host"
)


# ── Command construction (pure, no bwrap needed) ─────────────────────


def test_build_command_basic_structure(tmp_dir):
    config = SandboxConfig(enabled=True, backend="bubblewrap")
    argv = SandboxExecutor.build_bubblewrap_command("echo hi", config, tmp_dir)

    assert argv[0] == "bwrap"
    # child containment + dies with the parent tool call
    assert "--die-with-parent" in argv
    assert "--unshare-pid" in argv
    # command runs through a shell inside the jail, at the end of argv
    assert argv[-3:] == ["/bin/sh", "-c", "echo hi"]
    # chdir into the working directory
    assert "--chdir" in argv
    assert argv[argv.index("--chdir") + 1] == str(tmp_dir)


def test_build_command_binds_cwd_readwrite(tmp_dir):
    config = SandboxConfig(enabled=True, backend="bubblewrap")
    argv = SandboxExecutor.build_bubblewrap_command("true", config, tmp_dir)
    # the cwd is bind-mounted read-write (--bind, not --ro-bind)
    joined = " ".join(argv)
    assert f"--bind {tmp_dir} {tmp_dir}" in joined


def test_build_command_binds_allowed_dirs(tmp_dir):
    extra = tmp_dir / "extra"
    config = SandboxConfig(enabled=True, backend="bubblewrap", allowed_dirs=[extra])
    argv = SandboxExecutor.build_bubblewrap_command("true", config, tmp_dir)
    joined = " ".join(argv)
    assert f"--bind {extra} {extra}" in joined


def test_build_command_system_roots_are_readonly(tmp_dir):
    config = SandboxConfig(enabled=True, backend="bubblewrap")
    argv = SandboxExecutor.build_bubblewrap_command("true", config, tmp_dir)
    # /usr exists on any Linux host and must be bound READ-ONLY
    if Path("/usr").exists():
        assert "--ro-bind" in argv
        # /usr never appears as a writable --bind target
        rw_targets = [argv[i + 2] for i, a in enumerate(argv) if a == "--bind"]
        assert "/usr" not in rw_targets


# ── Network flag mapping ─────────────────────────────────────────────


def test_network_disabled_unshares_net(tmp_dir):
    config = SandboxConfig(enabled=True, backend="bubblewrap", network_enabled=False)
    argv = SandboxExecutor.build_bubblewrap_command("true", config, tmp_dir)
    assert "--unshare-net" in argv


def test_network_enabled_does_not_unshare_net(tmp_dir):
    config = SandboxConfig(enabled=True, backend="bubblewrap", network_enabled=True)
    argv = SandboxExecutor.build_bubblewrap_command("true", config, tmp_dir)
    assert "--unshare-net" not in argv


# ── Unavailable bubblewrap handling ──────────────────────────────────


@pytest.mark.asyncio
async def test_unavailable_fails_closed_by_default(tmp_dir):
    executor = SandboxExecutor()
    config = SandboxConfig(enabled=True, backend="bubblewrap")  # fallback_to_process=False
    with patch("d2c.sandbox.bubblewrap_available", return_value=False):
        result = await executor.execute_sandboxed("echo hi", config, cwd=tmp_dir)
    assert result.error is True
    assert result.sandboxed is False
    assert result.backend == "bubblewrap"
    assert "fail-closed" in result.output.lower()
    assert "hi" not in result.output  # command was not run


@pytest.mark.asyncio
async def test_unavailable_falls_back_to_process_when_configured(tmp_dir):
    executor = SandboxExecutor()
    config = SandboxConfig(enabled=True, backend="bubblewrap", fallback_to_process=True)
    cmd = "Write-Output hi" if platform.system() == "Windows" else "printf hi"
    with patch("d2c.sandbox.bubblewrap_available", return_value=False):
        result = await executor.execute_sandboxed(cmd, config, cwd=tmp_dir)
    # process backend ran the command
    assert result.backend == "process"
    assert "hi" in result.output


@pytest.mark.asyncio
async def test_execute_sandboxed_dispatches_to_bubblewrap(tmp_dir):
    executor = SandboxExecutor()
    config = SandboxConfig(enabled=True, backend="bubblewrap")
    with (
        patch("d2c.sandbox.bubblewrap_available", return_value=True),
        patch.object(executor, "_spawn_and_capture") as mock_spawn,
    ):
        from d2c.sandbox import SandboxResult

        async def _fake(*a, **k):
            return SandboxResult(output="ok", exit_code=0, sandboxed=True, backend="bubblewrap")

        mock_spawn.side_effect = _fake
        await executor.execute_sandboxed("echo hi", config, cwd=tmp_dir)
    # it built + ran a bwrap argv
    argv = mock_spawn.call_args[0][0]
    assert argv[0] == "bwrap"


# ── from_dict wiring ─────────────────────────────────────────────────


def test_from_dict_parses_bubblewrap_and_fallback():
    config = SandboxConfig.from_dict(
        {"enabled": True, "backend": "bubblewrap", "fallback_to_process": True}
    )
    assert config.backend == "bubblewrap"
    assert config.fallback_to_process is True


# ── Live confinement (skipped without bwrap) ─────────────────────────


@requires_bwrap
def test_live_cwd_write_succeeds(tmp_dir):
    executor = SandboxExecutor()
    config = SandboxConfig(enabled=True, backend="bubblewrap")

    async def run():
        return await executor.execute_sandboxed("echo hello > inside.txt", config, cwd=tmp_dir)

    result = asyncio.run(run())
    assert result.error is False, result.output
    # cwd is bind-mounted rw, so the write persists to the host
    assert (tmp_dir / "inside.txt").read_text().strip() == "hello"


@requires_bwrap
def test_live_write_outside_cwd_is_denied(tmp_dir):
    executor = SandboxExecutor()
    config = SandboxConfig(enabled=True, backend="bubblewrap")

    async def run():
        # /etc is bound read-only — a write there must fail inside the jail.
        return await executor.execute_sandboxed(
            "echo pwned > /etc/d2c_phase62_pwned", config, cwd=tmp_dir
        )

    result = asyncio.run(run())
    assert result.error is True
    assert not Path("/etc/d2c_phase62_pwned").exists()


@requires_bwrap
def test_live_sibling_dir_not_visible(tmp_dir):
    executor = SandboxExecutor()
    outside = tmp_dir.parent / "d2c_phase62_outside"
    outside.mkdir(exist_ok=True)
    try:
        config = SandboxConfig(enabled=True, backend="bubblewrap")

        async def run():
            return await executor.execute_sandboxed(
                f"echo pwned > {outside}/f.txt", config, cwd=tmp_dir
            )

        result = asyncio.run(run())
        assert result.error is True  # path not bound → not writable
        assert not (outside / "f.txt").exists()
    finally:
        import shutil

        shutil.rmtree(outside, ignore_errors=True)


# ── Doctor backend reporting ─────────────────────────────────────────


class _Cfg:
    def __init__(self, **kw):
        self.sandbox_enabled = kw.get("sandbox_enabled", False)
        self.sandbox_backend = kw.get("sandbox_backend", "process")
        self.sandbox_allow_network = kw.get("sandbox_allow_network", False)
        self.sandbox_fallback = kw.get("sandbox_fallback", False)


def test_doctor_disabled_shows_backend():
    from d2c.doctor import check_sandbox

    r = check_sandbox(_Cfg(sandbox_enabled=False, sandbox_backend="bubblewrap"))
    assert r.status == "pass"
    assert "disabled" in r.message
    assert "bubblewrap" in r.message


def test_doctor_bubblewrap_available_reports_os_level():
    from d2c.doctor import check_sandbox

    with patch("d2c.sandbox.bubblewrap_available", return_value=True):
        r = check_sandbox(_Cfg(sandbox_enabled=True, sandbox_backend="bubblewrap"))
    assert r.status == "pass"
    assert "bubblewrap" in r.message.lower()
    assert "no network" in r.message


def test_doctor_bubblewrap_missing_fails_closed():
    from d2c.doctor import check_sandbox

    with patch("d2c.sandbox.bubblewrap_available", return_value=False):
        r = check_sandbox(_Cfg(sandbox_enabled=True, sandbox_backend="bubblewrap"))
    assert r.status == "fail"
    assert "fail closed" in r.message.lower()


def test_doctor_bubblewrap_missing_with_fallback_warns():
    from d2c.doctor import check_sandbox

    with patch("d2c.sandbox.bubblewrap_available", return_value=False):
        r = check_sandbox(
            _Cfg(sandbox_enabled=True, sandbox_backend="bubblewrap", sandbox_fallback=True)
        )
    assert r.status == "warn"
    assert "fall back" in r.message.lower()


def test_doctor_unknown_backend_warns():
    from d2c.doctor import check_sandbox

    r = check_sandbox(_Cfg(sandbox_enabled=True, sandbox_backend="qubes"))
    assert r.status == "warn"
    assert "unknown backend" in r.message.lower()


# ── Config env wiring ────────────────────────────────────────────────


def test_config_reads_sandbox_backend_env(monkeypatch, tmp_dir, trusted_gate):
    from d2c.config import Config

    monkeypatch.setenv("D2C_SANDBOX", "1")
    monkeypatch.setenv("D2C_SANDBOX_BACKEND", "bubblewrap")
    monkeypatch.setenv("D2C_SANDBOX_NETWORK", "1")
    monkeypatch.setenv("D2C_SANDBOX_FALLBACK", "1")
    config = Config.load(cwd=tmp_dir)
    assert config.sandbox_enabled is True
    assert config.sandbox_backend == "bubblewrap"
    assert config.sandbox_allow_network is True
    assert config.sandbox_fallback is True


def test_config_sandbox_backend_defaults_to_process(monkeypatch, tmp_dir, trusted_gate):
    from d2c.config import Config

    monkeypatch.delenv("D2C_SANDBOX_BACKEND", raising=False)
    config = Config.load(cwd=tmp_dir)
    assert config.sandbox_backend == "process"
