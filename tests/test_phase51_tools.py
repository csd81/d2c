"""Phase 51: ApplyPatch + EnvInfo tools."""

import pytest

from d2c.tools import PermissionCategory, set_file_history_tracker
from d2c.tools.apply_patch import ApplyPatchTool, parse_unified_diff
from d2c.tools.env_info import EnvInfoTool
from d2c.tools.read_tool import FileReadTool
from d2c.tools.write_tool import clear_read_files


@pytest.fixture(autouse=True)
def _reset():
    clear_read_files()
    yield
    set_file_history_tracker(None)
    clear_read_files()


# ── Schemas / categories / pool ───────────────────────────────────────


def test_categories_and_schemas():
    assert EnvInfoTool().category == PermissionCategory.READ
    assert EnvInfoTool().is_concurrent_safe is True
    assert ApplyPatchTool().category == PermissionCategory.WRITE
    assert ApplyPatchTool().is_concurrent_safe is False
    assert "patch" in ApplyPatchTool().input_schema["properties"]


def test_apply_patch_description_guides_multifile_use():
    # Phase 68: the description should steer the model toward ApplyPatch for
    # coordinated multi-file edits and toward Edit for single edits.
    desc = ApplyPatchTool().to_api_format()["description"].lower()
    assert "multiple files" in desc
    assert "prefer edit" in desc


@pytest.mark.asyncio
async def test_new_tools_registered_in_pool(trusted_gate):
    from d2c.tools.pool import Config, assembleToolPool

    names = {t.name for t in await assembleToolPool(Config())}
    assert {"ApplyPatch", "EnvInfo"} <= names


# ── EnvInfo ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_envinfo_fields(tmp_dir, monkeypatch):
    monkeypatch.setenv("D2C_MODEL", "deepseek-chat")
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "tavily")
    res = await EnvInfoTool(cwd=tmp_dir).execute()
    m = res.metadata
    for k in (
        "d2c_version",
        "python",
        "platform",
        "cwd",
        "git_available",
        "model",
        "websearch_provider",
        "sandbox_enabled",
        "audit_log_enabled",
    ):
        assert k in m
    assert m["model"] == "deepseek-chat"
    assert m["websearch_provider"] == "tavily"
    assert str(tmp_dir) == m["cwd"]


@pytest.mark.asyncio
async def test_envinfo_never_exposes_secrets(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-appear-123")
    monkeypatch.setenv("D2C_WEBSEARCH_API_KEY", "tvly-should-not-appear")
    res = await EnvInfoTool().execute()
    blob = res.output + str(res.metadata)
    assert "sk-should-not-appear-123" not in blob
    assert "tvly-should-not-appear" not in blob
    assert "api_key" not in {k.lower() for k in res.metadata}


# ── ApplyPatch: parsing ───────────────────────────────────────────────


def test_parse_unified_diff_basic():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    fps = parse_unified_diff(patch)
    assert len(fps) == 1 and fps[0].path == "x.py"
    assert fps[0].hunks[0].old_start == 1


def test_parse_malformed_hunk_raises():
    with pytest.raises(ValueError):
        parse_unified_diff("--- a/x\n+++ b/x\n@@ garbage @@\n a\n")


# ── ApplyPatch: apply / safety ────────────────────────────────────────

_PATCH = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 20\n c = 3\n"


@pytest.mark.asyncio
async def test_apply_simple_patch_requires_read(tmp_dir, trusted_gate):
    f = tmp_dir / "app.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")
    tool = ApplyPatchTool(cwd=tmp_dir)

    blocked = await tool.execute(patch=_PATCH)
    assert blocked.error and "must Read" in blocked.output
    assert f.read_text() == "a = 1\nb = 2\nc = 3\n"

    await FileReadTool().execute(file_path=str(f))
    ok = await tool.execute(patch=_PATCH)
    assert not ok.error
    assert f.read_text() == "a = 1\nb = 20\nc = 3\n"
    assert ok.metadata["changed"] == ["app.py"]


@pytest.mark.asyncio
async def test_apply_rejects_absolute_and_traversal(tmp_dir, trusted_gate):
    tool = ApplyPatchTool(cwd=tmp_dir)
    abs_patch = f"--- a{tmp_dir}/x\n+++ b{tmp_dir}/x\n@@ -0,0 +1 @@\n+x\n"
    r1 = await tool.execute(patch=abs_patch)
    assert r1.error
    trav = "--- a/../evil.txt\n+++ b/../evil.txt\n@@ -0,0 +1 @@\n+x\n"
    r2 = await tool.execute(patch=trav)
    assert r2.error and "unsafe path" in r2.output


@pytest.mark.asyncio
async def test_apply_is_atomic_on_bad_hunk(tmp_dir, trusted_gate):
    a = tmp_dir / "a.txt"
    a.write_text("one\ntwo\n")
    b = tmp_dir / "b.txt"
    b.write_text("x\ny\n")
    await FileReadTool().execute(file_path=str(a))
    await FileReadTool().execute(file_path=str(b))
    tool = ApplyPatchTool(cwd=tmp_dir)

    # a.txt hunk applies; b.txt hunk does NOT match → whole patch fails, nothing written.
    patch = (
        "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n one\n-two\n+TWO\n"
        "--- a/b.txt\n+++ b/b.txt\n@@ -1,2 +1,2 @@\n NOPE\n-y\n+Y\n"
    )
    res = await tool.execute(patch=patch)
    assert res.error and "did not apply" in res.output
    assert a.read_text() == "one\ntwo\n"  # unchanged (atomic)
    assert b.read_text() == "x\ny\n"


@pytest.mark.asyncio
async def test_apply_checkpoints_and_fires_hook(tmp_dir):
    from d2c.file_history import FileHistory, FileHistoryTracker
    from d2c.hooks import HookDefinition, HookEvent, HookRegistry, HookResult, HookType
    from d2c.tools import set_active_hooks

    fh = FileHistory(tmp_dir / "hist", "s51", cwd=tmp_dir)
    set_file_history_tracker(FileHistoryTracker(fh))
    fired = []

    async def cb(ctx):
        fired.append(ctx)
        return HookResult()

    reg = HookRegistry()
    reg.register(
        HookDefinition(event=HookEvent.FILE_CHANGED, hook_type=HookType.CALLBACK, callback=cb)
    )
    set_active_hooks(reg)
    try:
        f = tmp_dir / "app.py"
        f.write_text("a = 1\nb = 2\nc = 3\n")
        await FileReadTool().execute(file_path=str(f))
        res = await ApplyPatchTool(cwd=tmp_dir).execute(patch=_PATCH)
        assert not res.error
        assert (fh.checkpoint_dir / "app.py").read_text() == "a = 1\nb = 2\nc = 3\n"
        assert len(fired) == 1 and fired[0]["tool"] == "ApplyPatch"
    finally:
        set_active_hooks(None)


@pytest.mark.asyncio
async def test_apply_creates_new_file(tmp_dir, trusted_gate):
    tool = ApplyPatchTool(cwd=tmp_dir)
    patch = "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1,2 @@\n+hello\n+world\n"
    res = await tool.execute(patch=patch)
    assert not res.error
    assert (tmp_dir / "new.txt").read_text() == "hello\nworld"
    assert res.metadata["file_count"] == 1
