"""Tests for Phase 31: Rich TUI / REPL Console.

Verifies D2CCompleter slash commands, file path completions, tool name
completions, and get_statusbar_text HTML rendering.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def mock_config():
    """Minimal config-like object for status bar testing."""
    cfg = MagicMock()
    cfg.model = "deepseek-v4-pro"
    cfg.permission_mode = "default"
    cfg.cwd = Path("/home/user/myproject")
    cfg.deepseek_api_key = "sk-should-never-appear-in-statusbar"
    return cfg


@pytest.fixture
def mock_session_store():
    """Session store with session_id for status bar."""
    store = MagicMock()
    store.session_id = "abc123-4567"
    return store


@pytest.fixture
def completer():
    """D2CCompleter in a temp directory with known files."""
    from d2c.main import D2CCompleter

    tmp = tempfile.mkdtemp()
    cwd = Path(tmp)
    # Create some files and dirs for completion testing
    (cwd / "README.md").write_text("")
    (cwd / "main.py").write_text("")
    (cwd / "utils.py").write_text("")
    (cwd / "src").mkdir(exist_ok=True)
    (cwd / "src" / "loop.py").write_text("")
    (cwd / "src" / "compact.py").write_text("")
    (cwd / "tests").mkdir(exist_ok=True)
    (cwd / "tests" / "test_main.py").write_text("")

    return D2CCompleter(cwd, ["Read", "Write", "Bash", "Glob", "Grep", "Agent"])


@pytest.fixture
def mock_document():
    """Factory for creating mock prompt_toolkit Document objects."""
    from unittest.mock import MagicMock

    def _make(text_before_cursor: str = ""):
        doc = MagicMock()
        doc.text_before_cursor = text_before_cursor
        doc.text = text_before_cursor
        return doc

    return _make


# ── Slash command completion tests ─────────────────────────────────────


class TestSlashCommandCompletions:
    def test_slash_yields_commands(self, completer, mock_document):
        """Typing '/' yields matching command completions."""
        doc = mock_document("/ex")
        completions = list(completer.get_completions(doc, None))
        assert len(completions) >= 1
        names = {c.text for c in completions}
        assert "/exit" in names

    def test_slash_empty_yields_all_commands(self, completer, mock_document):
        """Typing '/' alone yields all available commands."""
        doc = mock_document("/")
        completions = list(completer.get_completions(doc, None))
        assert len(completions) >= 5  # At least 5 slash commands
        names = {c.text for c in completions}
        assert "/exit" in names
        assert "/clear" in names
        assert "/help" in names

    def test_slash_no_match_returns_empty(self, completer, mock_document):
        """No matching slash command returns empty list."""
        doc = mock_document("/zzz")
        completions = list(completer.get_completions(doc, None))
        assert completions == []

    def test_slash_case_sensitive(self, completer, mock_document):
        """Slash commands are case-sensitive (lowercase expected)."""
        doc = mock_document("/EX")
        completions = list(completer.get_completions(doc, None))
        assert completions == []


# ── File path completion tests ─────────────────────────────────────────


class TestFilePathCompletions:
    def test_partial_match_yields_file(self, completer, mock_document):
        """Typing a partial path returns matching files in the workspace."""
        doc = mock_document("README")
        completions = list(completer.get_completions(doc, None))
        names = {c.text for c in completions}
        assert "README.md" in names

    def test_partial_match_yields_directory(self, completer, mock_document):
        """Typing a partial path returns matching directories."""
        doc = mock_document("sr")
        completions = list(completer.get_completions(doc, None))
        names = {c.text for c in completions}
        assert any("src" in name for name in names)

    def test_exact_file_name_single_match(self, completer, mock_document):
        """Exact file name prefix yields single completion."""
        doc = mock_document("main.py")
        completions = list(completer.get_completions(doc, None))
        names = {c.text for c in completions}
        assert "main.py" in names

    def test_no_match_returns_empty(self, completer, mock_document):
        """Non-matching prefix returns empty completions."""
        doc = mock_document("zzzzz_nonexistent")
        completions = list(completer.get_completions(doc, None))
        assert completions == []

    def test_empty_text_returns_all_files_and_dirs(self, completer, mock_document):
        """Empty text returns all visible files and directories."""
        doc = mock_document("")
        completions = list(completer.get_completions(doc, None))
        # Should have at least README.md, main.py, utils.py, src, tests
        assert len(completions) >= 5

    def test_ignores_common_patterns(self, completer, mock_document):
        """.git, node_modules, __pycache__ etc are excluded from completions."""
        # Create a node_modules directory — should be excluded
        node_dir = completer.cwd / "node_modules"
        node_dir.mkdir(exist_ok=True)
        (node_dir / "package.json").write_text("{}")

        doc = mock_document("node")
        completions = list(completer.get_completions(doc, None))
        names = {c.text for c in completions}
        assert "node_modules" not in names

    def test_subdirectory_files(self, completer, mock_document):
        """Files in subdirectories appear with relative paths."""
        doc = mock_document("tests/")
        completions = list(completer.get_completions(doc, None))
        # Should find test_main.py
        assert len(completions) >= 1


# ── Tool name completion tests ─────────────────────────────────────────


class TestToolCompletions:
    def test_tool_name_prefix_match(self, completer, mock_document):
        """Typing a tool name prefix yields matching tool completions."""
        doc = mock_document("Re")
        completions = list(completer.get_completions(doc, None))
        names = {c.text for c in completions}
        assert "Read" in names

    def test_tool_name_case_insensitive(self, completer, mock_document):
        """Tool name matching is case-insensitive."""
        doc = mock_document("read")
        completions = list(completer.get_completions(doc, None))
        names = {c.text for c in completions}
        assert "Read" in names

    def test_single_char_no_completions(self, completer, mock_document):
        """Single character doesn't trigger tool completions (too short)."""
        doc = mock_document("R")
        completions = list(completer.get_completions(doc, None))
        tool_names = {c.text for c in completions if c.display_meta == "tool"}
        assert tool_names == set()

    def test_full_tool_name_match(self, completer, mock_document):
        """Typing a full tool name yields that tool as a completion."""
        doc = mock_document("Bash")
        completions = list(completer.get_completions(doc, None))
        names = {c.text for c in completions}
        assert "Bash" in names


# ── Status bar rendering tests ─────────────────────────────────────────


class TestStatusBarRendering:
    def test_renders_session_id_short_form_and_mode(self, mock_config, mock_session_store):
        """Status bar HTML contains the 8-char session id prefix and mode."""
        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store)
        html_str = str(result)
        assert "abc123-4" in html_str  # first 8 chars of "abc123-4567"
        assert "abc123-4567" not in html_str  # full id is not shown
        assert "DEFAULT" in html_str
        assert "deepseek-v4-pro" in html_str

    def test_renders_without_session_store(self, mock_config):
        """Status bar works when session_store is None."""
        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, None)
        html_str = str(result)
        assert "<b>d2c</b>" in html_str
        assert "Session:" in html_str

    def test_renders_active_tasks(self, mock_config, mock_session_store):
        """Active task count appears in status bar when > 0."""
        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store, active_tasks=3)
        html_str = str(result)
        assert "Tasks: 3" in html_str

    def test_no_tasks_when_zero(self, mock_config, mock_session_store):
        """Tasks section is hidden when active_tasks is 0."""
        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store, active_tasks=0)
        html_str = str(result)
        assert "Tasks:" not in html_str

    def test_statusbar_is_html_type(self, mock_config, mock_session_store):
        """get_statusbar_text returns an HTML object."""
        from prompt_toolkit.formatted_text import HTML

        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store)
        assert isinstance(result, HTML)

    def test_renders_cwd_basename(self, mock_config, mock_session_store):
        """Status bar shows the cwd basename, not the full path."""
        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store, width=200)
        html_str = str(result)
        assert "myproject" in html_str
        assert "/home/user/myproject" not in html_str

    def test_renders_trust_status(self, mock_config, mock_session_store, trusted_gate):
        """Status bar reflects the workspace trust decision."""
        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store, width=200)
        assert "Trust: trusted" in str(result)

    def test_renders_untrusted_status(self, mock_config, mock_session_store, untrusted_gate):
        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store, width=200)
        assert "Trust: untrusted" in str(result)

    def test_no_secret_leakage(self, mock_config, mock_session_store):
        """The API key on config is never surfaced in the status bar."""
        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store, width=200)
        assert "sk-should-never-appear-in-statusbar" not in str(result)

    def test_long_model_name_is_field_truncated(self, mock_config, mock_session_store):
        """A very long model name is truncated with an ellipsis, not wrapped."""
        from d2c.main import get_statusbar_text

        mock_config.model = "a-very-long-model-name-that-exceeds-the-field-budget"
        result = get_statusbar_text(mock_config, mock_session_store, width=200)
        assert "…" in str(result)

    def test_narrow_width_drops_optional_fields_before_corrupting_html(
        self, mock_config, mock_session_store
    ):
        """A narrow terminal drops enrichments (cwd/trust/tasks) but still
        produces valid, parseable HTML — never a truncated/broken tag."""
        from prompt_toolkit.formatted_text import HTML

        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store, active_tasks=3, width=40)
        assert isinstance(result, HTML)
        html_str = str(result)
        assert "<b>d2c</b>" in html_str
        assert html_str.count("<style") == html_str.count("</style>")
        # Wide-only fields are the first to go under a tight budget.
        assert "myproject" not in html_str

    def test_extremely_narrow_width_still_produces_valid_html(
        self, mock_config, mock_session_store
    ):
        """Even a pathologically narrow width degrades to a hard-truncated
        core line rather than raising or emitting unparseable markup."""
        from prompt_toolkit.formatted_text import HTML

        from d2c.main import get_statusbar_text

        result = get_statusbar_text(mock_config, mock_session_store, width=10)
        assert isinstance(result, HTML)
        assert "<b>d2c</b>" in str(result)

    def test_statusbar_html_is_well_formed_for_prompt_toolkit(
        self, mock_config, mock_session_store
    ):
        """prompt_toolkit's HTML() must be able to parse the output without
        raising — this is what would break if truncation ever split a tag."""
        from d2c.main import get_statusbar_text

        for width in (10, 20, 40, 60, 80, 120, 200):
            result = get_statusbar_text(mock_config, mock_session_store, width=width)
            # to_formatted_text() forces prompt_toolkit to actually parse
            # the HTML; a corrupted tag raises here.
            result.__pt_formatted_text__()


# ── D2CCompleter initialization tests ──────────────────────────────────


class TestCompleterInit:
    def test_stores_cwd_and_tools(self):
        """Completer stores cwd and tools on initialization."""
        from d2c.main import D2CCompleter

        c = D2CCompleter(Path("/tmp"), ["Read", "Bash"])
        assert c.cwd == Path("/tmp")
        assert c.tools == ["Read", "Bash"]

    def test_commands_list(self):
        """Completer has the expected slash commands."""
        from d2c.main import D2CCompleter

        c = D2CCompleter(Path("/tmp"), [])
        assert "/exit" in c.commands
        assert "/clear" in c.commands
        assert "/help" in c.commands
        assert len(c.commands) >= 5
