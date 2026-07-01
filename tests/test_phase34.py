"""Phase 34: wiring of previously-inert subsystems.

Covers the runtime connections added in Phase 34: the Read→Write safety
handshake, file-history checkpoints, task lifecycle hooks, path-scoped rule
enforcement, and the new background-status / auto-memory tools.
"""

import pytest

from d2c.hooks import HookRegistry, HookDefinition, HookEvent, HookType, HookResult
from d2c.permissions import (
    PermissionEngine,
    PermissionMode,
    PermissionRequest,
    PermissionDecision,
)
from d2c.path_rules import PathScopedRules
from d2c.tools import (
    PermissionCategory,
    set_active_hooks,
    set_file_history_tracker,
    set_active_memory_loader,
)


# ── 1.1 Read-before-Write gate ────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_marks_file_so_write_is_allowed(tmp_dir, trusted_gate):
    from d2c.tools.read_tool import FileReadTool
    from d2c.tools.write_tool import FileWriteTool

    f = tmp_dir / "a.txt"
    f.write_text("original")

    # Writing an existing file without reading first must fail.
    res_blocked = await FileWriteTool().execute(file_path=str(f), content="x")
    assert res_blocked.error
    assert "Read the file first" in res_blocked.output

    # After Read, the same write succeeds.
    await FileReadTool().execute(file_path=str(f))
    res_ok = await FileWriteTool().execute(file_path=str(f), content="updated")
    assert not res_ok.error
    assert f.read_text() == "updated"


# ── 1.4 File-history checkpoints ──────────────────────────────────────

@pytest.mark.asyncio
async def test_write_creates_file_history_checkpoint(tmp_dir):
    from d2c.file_history import FileHistory, FileHistoryTracker
    from d2c.tools.write_tool import FileWriteTool, mark_file_read

    fh = FileHistory(tmp_dir / "hist", "sess1", cwd=tmp_dir)
    set_file_history_tracker(FileHistoryTracker(fh))
    try:
        f = tmp_dir / "b.txt"
        f.write_text("v1")
        mark_file_read(str(f))  # allow overwrite of existing file

        await FileWriteTool().execute(file_path=str(f), content="v2")

        checkpoint = fh.checkpoint_dir / "b.txt"
        assert checkpoint.exists()
        assert checkpoint.read_text() == "v1"  # original snapshot preserved

        # Rewind restores the original content.
        f.write_text("v3")
        FileHistory.rewind_session(tmp_dir / "hist", "sess1", cwd=tmp_dir)
        assert f.read_text() == "v1"
    finally:
        set_file_history_tracker(None)


# ── 2.1 Task lifecycle hooks ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_tools_fire_lifecycle_hooks():
    from d2c.tools.task_tools import TaskCreateTool, TaskUpdateTool

    fired: list[str] = []

    async def _cb(ctx):
        fired.append("event")
        return HookResult()

    reg = HookRegistry()
    reg.register(HookDefinition(event=HookEvent.TASK_CREATED, hook_type=HookType.CALLBACK, callback=_cb))
    reg.register(HookDefinition(event=HookEvent.TASK_COMPLETED, hook_type=HookType.CALLBACK, callback=_cb))
    set_active_hooks(reg)
    try:
        created = await TaskCreateTool().execute(subject="do it", description="d")
        task_id = created.metadata["task"]["id"]
        assert len(fired) == 1  # TASK_CREATED fired

        await TaskUpdateTool().execute(taskId=task_id, status="in_progress")
        assert len(fired) == 1  # not completed yet

        await TaskUpdateTool().execute(taskId=task_id, status="completed")
        assert len(fired) == 2  # TASK_COMPLETED fired
    finally:
        set_active_hooks(None)


# ── 3.1 Path-scoped rules reach the permission engine ─────────────────

def test_path_rules_enforced_by_engine(tmp_dir, trusted_gate):
    rules_dir = tmp_dir / ".d2c" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "no_read.md").write_text(
        '---\n'
        'rules:\n'
        '  - type: deny\n'
        '    pattern: "Read"\n'
        '    reason: "no reads in this tree"\n'
        'path: "."\n'
        '---\n'
    )

    # dontAsk would normally ALLOW everything; the path rule must still DENY.
    engine = PermissionEngine(mode=PermissionMode.DONT_ASK)
    engine.set_path_rules(PathScopedRules())

    req = PermissionRequest(
        tool_name="Read",
        tool_input={"file_path": str(tmp_dir / "x.txt")},
        tool_category=PermissionCategory.READ,
    )
    result = engine.evaluate(req)
    assert result.decision == PermissionDecision.DENY


def test_from_config_attaches_path_rules():
    from d2c.config import Config
    engine = PermissionEngine.from_config(Config(permission_mode="default"))
    assert engine._path_rules is not None


# ── 3.2 / 3.3 New tools registered ────────────────────────────────────

@pytest.mark.asyncio
async def test_new_tools_present_in_pool(trusted_gate):
    from d2c.tools.pool import Config as PoolConfig, assembleToolPool

    tools = await assembleToolPool(PoolConfig())
    names = {t.name for t in tools}
    assert "AgentStatus" in names
    assert "Remember" in names


@pytest.mark.asyncio
async def test_background_status_tool_lists_and_reports():
    from d2c.tools.background_status import BackgroundStatusTool

    res = await BackgroundStatusTool().execute()
    assert not res.error  # no subagents → friendly message, not an error

    res_unknown = await BackgroundStatusTool().execute(subagent_id="deadbeef")
    assert res_unknown.error  # unknown id reported as error
