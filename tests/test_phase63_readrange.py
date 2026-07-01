"""Phase 63: ReadRange — line-range reading for context economy."""

from __future__ import annotations

import pytest

from d2c.tools import PermissionCategory
from d2c.tools.read_range_tool import _MAX_RANGE_LINES, ReadRangeTool
from d2c.tools.write_tool import clear_read_files, is_file_read


@pytest.fixture(autouse=True)
def _reset_reads():
    clear_read_files()
    yield
    clear_read_files()


def _write(tmp_dir, name, n_lines):
    p = tmp_dir / name
    p.write_text("\n".join(f"line{i}" for i in range(1, n_lines + 1)))
    return p


# ── 1-3. Range reading + line-number toggle ──────────────────────────


@pytest.mark.asyncio
async def test_reads_requested_range(tmp_dir):
    f = _write(tmp_dir, "a.txt", 50)
    res = await ReadRangeTool().execute(file_path=str(f), start_line=10, end_line=13)
    assert not res.error
    assert res.metadata["start_line"] == 10
    assert res.metadata["end_line"] == 13
    assert res.metadata["returned_lines"] == 4
    assert res.metadata["total_lines"] == 50
    assert res.metadata["truncated"] is False
    # header + exactly the requested lines, nothing outside the range
    assert f"{f}:10-13" in res.output
    assert "line10" in res.output and "line13" in res.output
    assert "line9" not in res.output and "line14" not in res.output


@pytest.mark.asyncio
async def test_includes_line_numbers_by_default(tmp_dir):
    f = _write(tmp_dir, "a.txt", 20)
    res = await ReadRangeTool().execute(file_path=str(f), start_line=3, end_line=5)
    assert "3 | line3" in res.output
    assert "5 | line5" in res.output


@pytest.mark.asyncio
async def test_omits_line_numbers_when_requested(tmp_dir):
    f = _write(tmp_dir, "a.txt", 20)
    res = await ReadRangeTool().execute(
        file_path=str(f), start_line=3, end_line=5, include_line_numbers=False
    )
    lines = res.output.split("\n")
    assert lines[0] == f"{f}:3-5"  # header
    assert lines[1:] == ["line3", "line4", "line5"]
    assert "3 | " not in res.output


# ── 4. Invalid range rejected ────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_below_one_rejected(tmp_dir):
    f = _write(tmp_dir, "a.txt", 10)
    res = await ReadRangeTool().execute(file_path=str(f), start_line=0, end_line=5)
    assert res.error and "start_line must be >= 1" in res.output


@pytest.mark.asyncio
async def test_end_before_start_rejected(tmp_dir):
    f = _write(tmp_dir, "a.txt", 10)
    res = await ReadRangeTool().execute(file_path=str(f), start_line=8, end_line=3)
    assert res.error and ">= start_line" in res.output


@pytest.mark.asyncio
async def test_start_beyond_eof_rejected(tmp_dir):
    f = _write(tmp_dir, "a.txt", 10)
    res = await ReadRangeTool().execute(file_path=str(f), start_line=99, end_line=100)
    assert res.error and "exceeds file length" in res.output


# ── 5. Clamping / truncation ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_clamped_to_eof(tmp_dir):
    f = _write(tmp_dir, "a.txt", 30)
    res = await ReadRangeTool().execute(file_path=str(f), start_line=25, end_line=9999)
    assert not res.error
    assert res.metadata["end_line"] == 30
    assert res.metadata["returned_lines"] == 6
    assert res.metadata["truncated"] is False


@pytest.mark.asyncio
async def test_range_over_max_is_truncated(tmp_dir):
    f = _write(tmp_dir, "big.txt", _MAX_RANGE_LINES + 500)
    res = await ReadRangeTool().execute(
        file_path=str(f), start_line=1, end_line=_MAX_RANGE_LINES + 400
    )
    assert not res.error
    assert res.metadata["truncated"] is True
    assert res.metadata["returned_lines"] == _MAX_RANGE_LINES
    assert res.metadata["end_line"] == _MAX_RANGE_LINES
    assert "Truncated" in res.output


# ── 6-7. Missing file / directory ────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_file_clean_error(tmp_dir):
    res = await ReadRangeTool().execute(
        file_path=str(tmp_dir / "nope.txt"), start_line=1, end_line=5
    )
    assert res.error and "file not found" in res.output


@pytest.mark.asyncio
async def test_directory_clean_error(tmp_dir):
    res = await ReadRangeTool().execute(file_path=str(tmp_dir), start_line=1, end_line=5)
    assert res.error and "directory" in res.output


@pytest.mark.asyncio
async def test_relative_path_rejected(tmp_dir):
    res = await ReadRangeTool().execute(file_path="rel/a.txt", start_line=1, end_line=5)
    assert res.error and "absolute" in res.output.lower()


# ── 8-9. Read-before-Write integration (canonical) ───────────────────


@pytest.mark.asyncio
async def test_marks_canonical_file_read(tmp_dir):
    f = _write(tmp_dir, "a.txt", 10)
    assert not is_file_read(f)
    await ReadRangeTool().execute(file_path=str(f), start_line=1, end_line=3)
    assert is_file_read(f)


@pytest.mark.asyncio
async def test_readrange_then_edit_succeeds(tmp_dir, trusted_gate):
    from d2c.tools.edit_tool import FileEditTool

    f = _write(tmp_dir, "app.py", 10)
    # Edit without a prior read is blocked...
    blocked = await FileEditTool().execute(file_path=str(f), old_string="line5", new_string="LINE5")
    assert blocked.error and "Read the file first" in blocked.output

    # ...but ReadRange satisfies the guard.
    await ReadRangeTool().execute(file_path=str(f), start_line=1, end_line=10)
    ok = await FileEditTool().execute(file_path=str(f), old_string="line5", new_string="LINE5")
    assert not ok.error
    assert "LINE5" in f.read_text()


# ── 10. Symlink / alternate-spelling canonicalization ────────────────


@pytest.mark.asyncio
async def test_alt_spelling_marks_same_canonical_file(tmp_dir):
    sub = tmp_dir / "sub"
    sub.mkdir()
    f = _write(sub, "file.txt", 10)
    alt = tmp_dir / "sub" / ".." / "sub" / "file.txt"  # same realpath, different spelling
    await ReadRangeTool().execute(file_path=str(alt), start_line=1, end_line=3)
    assert is_file_read(f)  # canonicalized → the real file is marked read


@pytest.mark.asyncio
async def test_symlink_shares_read_identity_with_target(tmp_dir):
    target = _write(tmp_dir, "target.txt", 10)
    link = tmp_dir / "link.txt"
    link.symlink_to(target)
    await ReadRangeTool().execute(file_path=str(link), start_line=1, end_line=3)
    # Reading the symlink marks the real target read (same realpath), and an
    # unrelated file is NOT marked.
    assert is_file_read(target)
    other = _write(tmp_dir, "other.txt", 3)
    assert not is_file_read(other)


@pytest.mark.asyncio
async def test_error_result_does_not_mark_file_read(tmp_dir):
    f = _write(tmp_dir, "a.txt", 10)
    await ReadRangeTool().execute(file_path=str(f), start_line=50, end_line=60)  # out of range
    assert not is_file_read(f)


# ── 11. Pool registration + permission/category ──────────────────────


def test_category_and_concurrency():
    t = ReadRangeTool()
    assert t.category == PermissionCategory.READ
    assert t.is_concurrent_safe is True


@pytest.mark.asyncio
async def test_registered_in_pool(trusted_gate):
    from d2c.tools.pool import Config, assembleToolPool

    names = {t.name for t in await assembleToolPool(Config())}
    assert "ReadRange" in names


# ── 12. System-prompt lightweight-first guidance ─────────────────────


def test_system_prompt_mentions_lightweight_first_guidance():
    from d2c.context import getSystemPrompt

    prompt = getSystemPrompt()
    assert "ReadRange" in prompt
    assert "Context economy" in prompt
    # names at least one lightweight inspection tool as the preferred first step
    assert "Grep" in prompt and "CodeSymbols" in prompt
