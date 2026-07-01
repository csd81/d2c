"""Phase 37: stabilization / regression audit.

Regression coverage for the high-risk runtime paths wired in Phases 34-36:
Read/Edit/Write safety, sandbox pool wiring, path-scoped-rule enforcement
without rule accumulation, and file-history tracker re-pointing on session
switch.
"""

from functools import partial

import pytest

import d2c.main as main
from d2c.config import Config
from d2c.main import ReplState, SlashCommand, handle_slash_command, _pool_config_from
from d2c.permissions import (
    PermissionEngine,
    PermissionMode,
    PermissionRequest,
    PermissionDecision,
)
from d2c.path_rules import PathScopedRules
from d2c.persistence import SessionManager
from d2c.tools import (
    PermissionCategory,
    get_file_history_tracker,
    set_file_history_tracker,
)
from d2c.tools.pool import Config as PoolConfig, assembleToolPool


@pytest.fixture(autouse=True)
def _reset_tracker():
    yield
    set_file_history_tracker(None)


# ── 3. Read/Edit/Write safety audit ───────────────────────────────────

@pytest.mark.asyncio
async def test_edit_requires_prior_read(tmp_dir, trusted_gate):
    from d2c.tools.edit_tool import FileEditTool

    f = tmp_dir / "code.py"
    f.write_text("x = 1\n")
    res = await FileEditTool().execute(file_path=str(f), old_string="x = 1", new_string="x = 2")
    assert res.error
    assert "Read the file first" in res.output
    assert f.read_text() == "x = 1\n"  # unchanged


@pytest.mark.asyncio
async def test_read_then_edit_succeeds(tmp_dir, trusted_gate):
    from d2c.tools.read_tool import FileReadTool
    from d2c.tools.edit_tool import FileEditTool

    f = tmp_dir / "code.py"
    f.write_text("x = 1\n")
    await FileReadTool().execute(file_path=str(f))
    res = await FileEditTool().execute(file_path=str(f), old_string="x = 1", new_string="x = 2")
    assert not res.error
    assert f.read_text() == "x = 2\n"


# ── 5. Sandbox wiring audit ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pool_config_sandbox_off_by_default(tmp_dir, trusted_gate):
    tools = await assembleToolPool(_pool_config_from(Config(cwd=tmp_dir)))
    bash = next(t for t in tools if t.name == "Bash")
    assert bash._sandbox_config is not None
    assert bash._sandbox_config.enabled is False  # default: no behavior change


@pytest.mark.asyncio
async def test_pool_config_wires_sandbox_when_enabled(tmp_dir, trusted_gate):
    tools = await assembleToolPool(_pool_config_from(Config(cwd=tmp_dir, sandbox_enabled=True)))
    bash = next(t for t in tools if t.name == "Bash")
    assert bash._sandbox_config is not None
    assert bash._sandbox_config.enabled is True


# ── 7. Path-rule enforcement without accumulation ──────────────────────

def _read_req(path) -> PermissionRequest:
    return PermissionRequest(
        tool_name="Read",
        tool_input={"file_path": str(path)},
        tool_category=PermissionCategory.READ,
    )


def test_path_rules_do_not_accumulate(tmp_dir, trusted_gate):
    rules_dir = tmp_dir / ".d2c" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "deny.md").write_text(
        '---\nrules:\n  - type: deny\n    pattern: "Read"\n    reason: "no"\npath: "."\n---\n'
    )

    engine = PermissionEngine(mode=PermissionMode.DONT_ASK)
    engine.set_path_rules(PathScopedRules())
    baseline = len(engine.rules)

    req = _read_req(tmp_dir / "x.txt")
    decisions = [engine.evaluate(req).decision for _ in range(5)]

    # Enforced consistently every time...
    assert decisions == [PermissionDecision.DENY] * 5
    # ...and path-rule consultation never mutates the engine's global rule list.
    assert len(engine.rules) == baseline


def test_path_rules_absent_falls_through_to_mode(tmp_dir, trusted_gate):
    engine = PermissionEngine(mode=PermissionMode.DONT_ASK)
    engine.set_path_rules(PathScopedRules())
    # No .d2c/rules present → path consult returns None → mode default ALLOW.
    assert engine.evaluate(_read_req(tmp_dir / "x.txt")).decision == PermissionDecision.ALLOW


# ── 4. File-history tracker re-points on session switch ────────────────

@pytest.mark.asyncio
async def test_clear_repoints_file_history_tracker(tmp_dir, monkeypatch):
    monkeypatch.setattr(main, "SessionManager", partial(SessionManager, base_dir=tmp_dir))
    mgr = SessionManager(base_dir=tmp_dir)
    old = mgr.create_session(tmp_dir)

    state = ReplState(config=Config(cwd=tmp_dir), session_store=old, conversation=[])
    await handle_slash_command(SlashCommand(name="/clear"), state)

    tracker = get_file_history_tracker()
    assert tracker is not None
    # Tracker now points at the NEW active session, not the old one.
    assert tracker.file_history._session_id == state.session_store.session_id
    assert tracker.file_history._session_id != old.session_id
