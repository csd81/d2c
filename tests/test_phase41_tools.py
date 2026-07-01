"""Phase 41: new built-in tools (git / filesystem / structured edits)."""

import asyncio
import json
import subprocess

import pytest

from d2c.tools import PermissionCategory, set_file_history_tracker
from d2c.tools.git_tools import GitStatusTool, GitDiffTool
from d2c.tools.fs_tools import ListDirTool, FileInfoTool
from d2c.tools.structured_edit import ReplaceManyTool, JsonEditTool


@pytest.fixture(autouse=True)
def _reset_tracker():
    yield
    set_file_history_tracker(None)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def git_repo(tmp_dir):
    _git(tmp_dir, "init")
    _git(tmp_dir, "config", "user.email", "t@e.com")
    _git(tmp_dir, "config", "user.name", "T")
    (tmp_dir / "a.txt").write_text("one\n")
    _git(tmp_dir, "add", "a.txt")
    _git(tmp_dir, "commit", "-m", "init")
    return tmp_dir


# ── Categories / schemas ──────────────────────────────────────────────

def test_categories_and_concurrency():
    assert GitStatusTool().category == PermissionCategory.READ
    assert GitStatusTool().is_concurrent_safe is True
    assert FileInfoTool().is_concurrent_safe is True
    assert ReplaceManyTool().category == PermissionCategory.WRITE
    assert ReplaceManyTool().is_concurrent_safe is False
    assert JsonEditTool().category == PermissionCategory.WRITE
    assert JsonEditTool().is_concurrent_safe is False
    for t in (GitStatusTool(), GitDiffTool(), ListDirTool(), FileInfoTool(),
              ReplaceManyTool(), JsonEditTool()):
        assert "required" in t.input_schema


# ── GitStatus / GitDiff ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_git_status_clean_and_dirty(git_repo):
    res = await GitStatusTool(cwd=git_repo).execute()
    assert not res.error
    assert res.metadata["clean"] is True

    (git_repo / "a.txt").write_text("two\n")       # unstaged change
    (git_repo / "new.txt").write_text("x\n")       # untracked
    res2 = await GitStatusTool(cwd=git_repo).execute()
    assert res2.metadata["clean"] is False
    assert any("a.txt" in u for u in res2.metadata["unstaged"])
    assert "new.txt" in res2.metadata["untracked"]

    _git(git_repo, "add", "new.txt")               # staged
    res3 = await GitStatusTool(cwd=git_repo).execute()
    assert any("new.txt" in s for s in res3.metadata["staged"])


@pytest.mark.asyncio
async def test_git_status_outside_repo(tmp_dir):
    res = await GitStatusTool(cwd=tmp_dir).execute()
    assert res.error
    assert "not a git repository" in res.output.lower()


@pytest.mark.asyncio
async def test_git_diff_unstaged_staged_and_path(git_repo):
    tool = GitDiffTool(cwd=git_repo)
    assert (await tool.execute()).output == "(no changes)"

    (git_repo / "a.txt").write_text("two\n")
    unstaged = await tool.execute()
    assert "two" in unstaged.output and unstaged.metadata["diff_bytes"] > 0

    _git(git_repo, "add", "a.txt")
    assert "(no changes)" in (await tool.execute()).output   # nothing unstaged now
    staged = await tool.execute(staged=True)
    assert "two" in staged.output and staged.metadata["staged"] is True

    scoped = await tool.execute(path="a.txt", staged=True)
    assert "a.txt" in scoped.output


# ── ListDir / FileInfo ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_listdir_hidden_depth_and_missing(tmp_dir):
    (tmp_dir / "visible.txt").write_text("x")
    (tmp_dir / ".hidden").write_text("y")
    sub = tmp_dir / "sub"
    sub.mkdir()
    (sub / "deep.txt").write_text("z")

    res = await ListDirTool().execute(path=str(tmp_dir))
    names = {e["name"] for e in res.metadata["entries"]}
    assert "visible.txt" in names and ".hidden" not in names
    assert "sub/deep.txt" not in names          # depth 1

    res_hidden = await ListDirTool().execute(path=str(tmp_dir), show_hidden=True)
    assert ".hidden" in {e["name"] for e in res_hidden.metadata["entries"]}

    res_deep = await ListDirTool().execute(path=str(tmp_dir), depth=2)
    assert "sub/deep.txt" in {e["name"] for e in res_deep.metadata["entries"]}

    missing = await ListDirTool().execute(path=str(tmp_dir / "nope"))
    assert missing.error
    rel = await ListDirTool().execute(path="relative/dir")
    assert rel.error and "absolute" in rel.output.lower()


@pytest.mark.asyncio
async def test_fileinfo_file_dir_missing(tmp_dir):
    f = tmp_dir / "data.txt"
    f.write_text("hello")
    res = await FileInfoTool().execute(path=str(f))
    assert res.metadata["exists"] is True
    assert res.metadata["type"] == "file"
    assert res.metadata["size"] == 5
    assert len(res.metadata["sha256"]) == 64

    d = await FileInfoTool().execute(path=str(tmp_dir))
    assert d.metadata["type"] == "dir"

    missing = await FileInfoTool().execute(path=str(tmp_dir / "nope"))
    assert missing.metadata["exists"] is False
    assert missing.error is False   # informative, not an error


# ── ReplaceMany (WRITE invariants) ────────────────────────────────────

@pytest.mark.asyncio
async def test_replace_many_requires_read(tmp_dir, trusted_gate):
    f = tmp_dir / "code.py"
    f.write_text("a = 1\nb = 2\n")
    res = await ReplaceManyTool().execute(
        file_path=str(f), replacements=[{"old_string": "a = 1", "new_string": "a = 9"}])
    assert res.error and "Read the file first" in res.output
    assert f.read_text() == "a = 1\nb = 2\n"


@pytest.mark.asyncio
async def test_replace_many_atomic_and_checkpoints(tmp_dir):
    from d2c.tools.read_tool import FileReadTool
    from d2c.file_history import FileHistory, FileHistoryTracker

    fh = FileHistory(tmp_dir / "hist", "s41", cwd=tmp_dir)
    set_file_history_tracker(FileHistoryTracker(fh))

    f = tmp_dir / "code.py"
    f.write_text("a = 1\nb = 2\n")
    await FileReadTool().execute(file_path=str(f))

    # One replacement can't apply → file unchanged, nothing written.
    bad = await ReplaceManyTool().execute(file_path=str(f), replacements=[
        {"old_string": "a = 1", "new_string": "a = 9"},
        {"old_string": "NOPE", "new_string": "x"},
    ])
    assert bad.error
    assert f.read_text() == "a = 1\nb = 2\n"

    # All apply → written atomically + checkpoint of the original created.
    ok = await ReplaceManyTool().execute(file_path=str(f), replacements=[
        {"old_string": "a = 1", "new_string": "a = 9"},
        {"old_string": "b = 2", "new_string": "b = 8"},
    ])
    assert not ok.error and ok.metadata["replacements_applied"] == 2
    assert f.read_text() == "a = 9\nb = 8\n"
    assert (fh.checkpoint_dir / "code.py").read_text() == "a = 1\nb = 2\n"


# ── JsonEdit (WRITE invariants) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_json_edit_set_delete_and_invalid(tmp_dir):
    from d2c.tools.read_tool import FileReadTool

    f = tmp_dir / "pkg.json"
    f.write_text(json.dumps({"name": "x", "scripts": {"test": "old"}}))
    await FileReadTool().execute(file_path=str(f))

    r1 = await JsonEditTool().execute(file_path=str(f), operation="set", path="scripts.test", value="pytest")
    assert not r1.error
    assert json.loads(f.read_text())["scripts"]["test"] == "pytest"

    r2 = await JsonEditTool().execute(file_path=str(f), operation="delete", path="name")
    assert not r2.error
    assert "name" not in json.loads(f.read_text())

    r3 = await JsonEditTool().execute(file_path=str(f), operation="set", path="a.b.c", value=1)
    assert r3.error and "not found" in r3.output.lower()


@pytest.mark.asyncio
async def test_json_edit_requires_read_and_rejects_bad_json(tmp_dir, trusted_gate):
    f = tmp_dir / "cfg.json"
    f.write_text('{"k": 1}')
    # No prior Read → blocked.
    blocked = await JsonEditTool().execute(file_path=str(f), operation="set", path="k", value=2)
    assert blocked.error and "Read the file first" in blocked.output

    from d2c.tools.read_tool import FileReadTool
    bad = tmp_dir / "broken.json"
    bad.write_text("{not json")
    await FileReadTool().execute(file_path=str(bad))
    res = await JsonEditTool().execute(file_path=str(bad), operation="set", path="k", value=2)
    assert res.error and "invalid json" in res.output.lower()
