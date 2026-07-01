"""Tests for Phase 6: Memory System — CLAUDE.md hierarchy, @include, auto memory, lazy loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from d2c.memory import (
    AutoMemoryStore,
    LazyMemoryLoader,
    MemoryFile,
    MemoryLevel,
    assembleMemoryContent,
    loadClaudeMdHierarchy,
    parseIncludePath,
    processMemoryFile,
)

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def tmp_home(monkeypatch, tmp_path):
    """Mock home directory to tmp_path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def memory_store(tmp_home):
    store = AutoMemoryStore()
    store.MEMORY_DIR = tmp_home / ".d2c" / "memory"
    store.INDEX_FILE = store.MEMORY_DIR / "MEMORY.md"
    store.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return store


# ── MemoryFile / MemoryLevel tests ─────────────────────────────────────


class TestMemoryFile:
    def test_memory_file_creation(self):
        mf = MemoryFile(
            path=Path("/test/CLAUDE.md"),
            level=MemoryLevel.PROJECT,
            content="Test instructions",
            priority=5,
        )
        assert mf.path == Path("/test/CLAUDE.md")
        assert mf.level == MemoryLevel.PROJECT
        assert mf.content == "Test instructions"
        assert mf.priority == 5


class TestMemoryLevel:
    def test_level_values(self):
        assert MemoryLevel.MANAGED.value == 1
        assert MemoryLevel.USER.value == 2
        assert MemoryLevel.PROJECT.value == 3
        assert MemoryLevel.LOCAL.value == 4

    def test_level_comparison(self):
        assert MemoryLevel.LOCAL.value > MemoryLevel.PROJECT.value


# ── assembleMemoryContent tests ────────────────────────────────────────


class TestAssembleMemoryContent:
    def test_single_file(self):
        files = [
            MemoryFile(Path("/p/CLAUDE.md"), MemoryLevel.PROJECT, "content", 0),
        ]
        result = assembleMemoryContent(files)
        assert "PROJECT" in result
        assert "CLAUDE.md" in result
        assert "content" in result

    def test_multiple_files_sorted_by_priority(self):
        files = [
            MemoryFile(Path("/b.md"), MemoryLevel.PROJECT, "low", 0),
            MemoryFile(Path("/a.md"), MemoryLevel.PROJECT, "high", 1),
        ]
        result = assembleMemoryContent(files)
        # "low" should appear before "high" in output (sorted by priority)
        assert result.index("low") < result.index("high")

    def test_sections_separated(self):
        files = [
            MemoryFile(Path("/a.md"), MemoryLevel.PROJECT, "first", 0),
            MemoryFile(Path("/b.md"), MemoryLevel.LOCAL, "second", 1),
        ]
        result = assembleMemoryContent(files)
        assert "---" in result


# ── @include directive tests ───────────────────────────────────────────


class TestParseIncludePath:
    def test_relative(self):
        base = Path("/base").resolve()
        result = parseIncludePath("@./sub/file.md", base)
        assert result == base / "sub" / "file.md"

    def test_parent_relative(self):
        base = Path("/base/dir").resolve()
        result = parseIncludePath("@../other/file.md", base)
        assert result == base.parent / "other" / "file.md"

    def test_home_relative(self, tmp_home):
        result = parseIncludePath("@~/config.md", Path("/base"))
        assert result == tmp_home / "config.md"

    def test_absolute(self):
        result = parseIncludePath("@/etc/config.md", Path("/base"))
        # parseIncludePath returns the path as-is for absolute paths (no resolve)
        assert str(result) == str(Path("/etc/config.md"))

    def test_bare_name(self):
        base = Path("/base").resolve()
        result = parseIncludePath("@file.md", base)
        assert result == base / "file.md"

    def test_empty(self):
        assert parseIncludePath("@", Path("/base")) is None
        assert parseIncludePath("@  ", Path("/base")) is None


class TestProcessMemoryFile:
    def test_no_includes(self):
        content = "line1\nline2"
        result = processMemoryFile(content, Path("/base"), set())
        assert result == content

    def test_include_relative(self, tmp_path):
        included = tmp_path / "included.md"
        included.write_text("included content")
        content = "before\n@./included.md\nafter"
        result = processMemoryFile(content, tmp_path, set())
        assert "before" in result
        assert "included content" in result
        assert "after" in result

    def test_include_not_in_code_block(self, tmp_path):
        included = tmp_path / "lib.md"
        included.write_text("lib content")
        content = "```python\n@./lib.md\n```\n@./lib.md"
        # @include inside code block should NOT be processed
        # @include outside should be processed
        result = processMemoryFile(content, tmp_path, set())
        assert "lib content" in result

    def test_circular_include_prevented(self, tmp_path):
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("content A\n@./b.md")
        b.write_text("content B\n@./a.md")

        processed: set[str] = set()
        result = processMemoryFile(a.read_text(), tmp_path, processed)
        assert "content A" in result
        assert "content B" in result
        # Should not infinite loop

    def test_nonexistent_include_silently_ignored(self, tmp_path):
        content = "before\n@./nonexistent.md\nafter"
        result = processMemoryFile(content, tmp_path, set())
        assert "before" in result
        assert "after" in result
        assert "nonexistent" not in result


# ── CLAUDE.md hierarchy tests ──────────────────────────────────────────


class TestLoadClaudeMdHierarchy:
    def test_empty_when_no_files(self, tmp_path):
        result = loadClaudeMdHierarchy(tmp_path)
        assert result == ""

    def test_loads_project_claude_md(self, tmp_path):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("Project instructions")
        result = loadClaudeMdHierarchy(tmp_path)
        assert "Project instructions" in result

    def test_loads_dot_d2c_claude_md(self, tmp_path):
        d2c_dir = tmp_path / ".d2c"
        d2c_dir.mkdir()
        claude_md = d2c_dir / "CLAUDE.md"
        claude_md.write_text("D2C instructions")
        result = loadClaudeMdHierarchy(tmp_path)
        assert "D2C instructions" in result

    def test_loads_claude_local_md(self, tmp_path):
        local_md = tmp_path / "CLAUDE.local.md"
        local_md.write_text("Local instructions")
        result = loadClaudeMdHierarchy(tmp_path)
        assert "Local instructions" in result

    def test_loads_rules_directory(self, tmp_path):
        rules_dir = tmp_path / ".d2c" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "01-rule.md").write_text("Rule 1")
        (rules_dir / "02-rule.md").write_text("Rule 2")
        result = loadClaudeMdHierarchy(tmp_path)
        assert "Rule 1" in result
        assert "Rule 2" in result

    def test_user_memory_from_home(self, tmp_home):
        d2c_dir = tmp_home / ".d2c"
        d2c_dir.mkdir()
        claude_md = d2c_dir / "CLAUDE.md"
        claude_md.write_text("User instructions")
        result = loadClaudeMdHierarchy(tmp_home)
        assert "User instructions" in result

    def test_priority_order_root_to_cwd(self, tmp_path):
        parent = tmp_path / "parent"
        parent.mkdir()
        child = parent / "child"
        child.mkdir()

        (tmp_path / "CLAUDE.md").write_text("root")
        (child / "CLAUDE.md").write_text("child")

        result = loadClaudeMdHierarchy(child)
        root_pos = result.index("root")
        child_pos = result.index("child")
        # Child should have higher priority (appears later)
        assert child_pos > root_pos

    def test_includes_memory_level_label(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("test")
        result = loadClaudeMdHierarchy(tmp_path)
        assert "PROJECT" in result  # level label in HTML comment


# ── Auto memory tests ──────────────────────────────────────────────────


class TestAutoMemoryStore:
    def test_save_creates_file(self, memory_store):
        filepath = memory_store.save("test", "user", "A test memory", "Content here")
        assert filepath.exists()
        assert "Content here" in filepath.read_text()

    def test_save_writes_frontmatter(self, memory_store):
        filepath = memory_store.save("myname", "feedback", "A description", "Body text")
        text = filepath.read_text()
        assert "name: myname" in text
        assert "description: A description" in text
        assert "type: feedback" in text
        assert "Body text" in text

    def test_save_updates_index(self, memory_store):
        memory_store.save("test1", "user", "First memory", "Content")
        index = memory_store.INDEX_FILE.read_text()
        assert "[test1]" in index

    def test_save_updates_existing_entry(self, memory_store):
        memory_store.save("test1", "user", "First", "Content")
        memory_store.save("test1", "user", "Updated", "New content")
        index = memory_store.INDEX_FILE.read_text()
        assert "Updated" in index
        assert "[test1]" in index

    def test_load_returns_content(self, memory_store):
        memory_store.save("loadtest", "project", "Test desc", "Memory body")
        result = memory_store.load("loadtest")
        assert result is not None
        assert "Memory body" in result

    def test_load_nonexistent(self, memory_store):
        assert memory_store.load("nonexistent") is None

    def test_delete_removes_file_and_index(self, memory_store):
        filepath = memory_store.save("delme", "user", "To delete", "Content")
        assert filepath.exists()
        assert memory_store.delete("delme") is True
        assert not filepath.exists()
        index = memory_store.INDEX_FILE.read_text()
        assert "delme" not in index

    def test_delete_nonexistent(self, memory_store):
        assert memory_store.delete("nonexist") is False

    def test_save_empty_content(self, memory_store):
        filepath = memory_store.save("empty", "user", "Empty", "")
        # Should still create the file with frontmatter
        assert filepath.exists()

    def test_sanitize_special_chars(self, memory_store):
        filepath = memory_store.save("test/name with spaces", "user", "desc", "body")
        assert "/" not in str(filepath.name)


# ── Lazy memory loader tests ───────────────────────────────────────────


class TestLazyMemoryLoader:
    def test_returns_none_for_cwd(self, tmp_path):
        loader = LazyMemoryLoader(tmp_path)
        result = loader.on_file_accessed(tmp_path / "file.txt")
        assert result is None

    def test_returns_none_for_parent_of_cwd(self, tmp_path):
        child = tmp_path / "sub"
        child.mkdir()
        loader = LazyMemoryLoader(child)
        result = loader.on_file_accessed(tmp_path / "other.txt")
        assert result is None  # above cwd

    def test_loads_claude_md_in_nested_dir(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "CLAUDE.md").write_text("Nested instructions")

        loader = LazyMemoryLoader(tmp_path)
        result = loader.on_file_accessed(subdir / "test.py")
        assert result is not None
        assert "Nested instructions" in result

    def test_loads_claude_local_md_in_nested_dir(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "CLAUDE.local.md").write_text("Local nested")

        loader = LazyMemoryLoader(tmp_path)
        result = loader.on_file_accessed(subdir / "test.py")
        assert "Local nested" in result

    def test_loads_rules_in_nested_dir(self, tmp_path):
        subdir = tmp_path / "sub"
        rules = subdir / ".d2c" / "rules"
        rules.mkdir(parents=True)
        (rules / "rule.md").write_text("Nested rule")

        loader = LazyMemoryLoader(tmp_path)
        result = loader.on_file_accessed(subdir / "test.py")
        assert "Nested rule" in result

    def test_only_loads_once(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "CLAUDE.md").write_text("Nested")

        loader = LazyMemoryLoader(tmp_path)
        result1 = loader.on_file_accessed(subdir / "a.py")
        assert result1 is not None

        result2 = loader.on_file_accessed(subdir / "b.py")
        assert result2 is None  # Already loaded

    def test_loads_deeply_nested(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "CLAUDE.md").write_text("Deep instructions")

        loader = LazyMemoryLoader(tmp_path)
        result = loader.on_file_accessed(deep / "test.py")
        assert result is not None
        assert "Deep instructions" in result


# ── Integration test ───────────────────────────────────────────────────


class TestGetUserContext:
    def test_includes_memory_when_available(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("Test memory")
        from d2c.config import Config

        config = Config(cwd=tmp_path)
        from d2c.context import getUserContext

        result = getUserContext(config)
        assert "Test memory" in result
        assert "Today's date" in result

    def test_no_memory_when_empty(self, tmp_path):
        from d2c.config import Config

        config = Config(cwd=tmp_path)
        from d2c.context import getUserContext

        result = getUserContext(config)
        assert "Today's date" in result
