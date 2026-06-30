"""Tests for Phase 21: Path-Scoped Rules."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from d2c.path_rules import (
    PathScopedRules,
    PathRuleResult,
    parse_yaml_frontmatter,
)
from d2c.permissions import PermissionRule, RuleType, PermissionEngine, PermissionMode, PermissionRequest
from d2c.tools import PermissionCategory
from d2c.memory import LazyMemoryLoader


# ── YAML frontmatter parser tests ───────────────────────────────────────

class TestYamlFrontmatter:
    def test_parses_basic_frontmatter(self):
        text = "---\nkey: value\nfoo: bar\n---\nBody text here"
        fm, body = parse_yaml_frontmatter(text)
        assert fm.get("key") == "value"
        assert fm.get("foo") == "bar"
        assert "Body text here" in body

    def test_no_frontmatter_returns_empty(self):
        text = "Just markdown, no frontmatter"
        fm, body = parse_yaml_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_list_in_frontmatter(self):
        text = "---\nrules:\n  - type: deny\n    pattern: Bash\n  - type: allow\n    pattern: Read\n---\n"
        fm, body = parse_yaml_frontmatter(text)
        assert isinstance(fm.get("rules"), list)
        assert len(fm["rules"]) == 2
        assert fm["rules"][0]["type"] == "deny"

    def test_unclosed_frontmatter(self):
        text = "---\nkey: value\nNo closing delimiter"
        fm, body = parse_yaml_frontmatter(text)
        assert fm == {}
        assert "No closing delimiter" in body


# ── PathScopedRules tests ───────────────────────────────────────────────

class TestPathScopedRules:
    def test_loads_rules_from_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rules_dir = root / ".d2c" / "rules"
            rules_dir.mkdir(parents=True)

            (rules_dir / "security.md").write_text(
                "---\n"
                "rules:\n"
                "  - type: deny\n"
                "    pattern: Bash\n"
                "    reason: No shell in this dir\n"
                "---\n"
                "# Security rules\n"
            )

            psr = PathScopedRules()
            results = psr.on_directory_accessed(root)
            assert len(results) == 1
            assert results[0].rules[0].rule_type == RuleType.DENY
            assert results[0].rules[0].pattern == "Bash"
            assert "Security rules" in (results[0].content or "")

    def test_loaded_dirs_not_loaded_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rules_dir = root / ".d2c" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "rules.md").write_text("---\nrules:\n  - type: deny\n    pattern: Write\n---\n")

            psr = PathScopedRules()
            results1 = psr.on_directory_accessed(root)
            results2 = psr.on_directory_accessed(root)
            assert len(results1) == 1
            assert len(results2) == 0  # Already loaded

    def test_child_rules_dont_affect_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_a = root / "a"
            child_b = root / "b"
            child_a.mkdir()
            child_b.mkdir()

            rules_a = child_a / ".d2c" / "rules"
            rules_a.mkdir(parents=True)
            (rules_a / "rules.md").write_text("---\nrules:\n  - type: deny\n    pattern: Bash\n---\n")

            rules_b = child_b / ".d2c" / "rules"
            rules_b.mkdir(parents=True)
            (rules_b / "rules.md").write_text("---\nrules:\n  - type: allow\n    pattern: Read\n---\n")

            psr = PathScopedRules()
            psr.on_directory_accessed(child_a)
            psr.on_directory_accessed(child_b)

            # Check rules for a file in child_a only
            rules_for_a = psr.get_rules_for_path(child_a / "file.py")
            rule_types_a = {r.rule_type for r in rules_for_a}
            assert RuleType.DENY in rule_types_a
            # Should NOT include child_b's allow rule
            assert RuleType.ALLOW not in rule_types_a

    def test_empty_rules_directory_no_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rules_dir = root / ".d2c" / "rules"
            rules_dir.mkdir(parents=True)
            # No .md files

            psr = PathScopedRules()
            results = psr.on_directory_accessed(root)
            assert results == []

    def test_no_rules_directory_no_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # No .d2c/rules directory at all

            psr = PathScopedRules()
            results = psr.on_directory_accessed(root)
            assert results == []
            assert psr.is_loaded(root)

    def test_parent_rules_apply_to_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "subdir"
            child.mkdir()

            rules_dir = root / ".d2c" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "global.md").write_text("---\nrules:\n  - type: deny\n    pattern: Write\n---\n")

            psr = PathScopedRules()
            psr.on_directory_accessed(child)  # Loads both child (no rules) and root (has rules)

            rules = psr.get_rules_for_path(child / "data.py")
            assert len(rules) >= 1
            assert rules[0].pattern == "Write"

    def test_multiple_rules_in_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rules_dir = root / ".d2c" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "comprehensive.md").write_text(
                "---\n"
                "rules:\n"
                "  - type: deny\n"
                "    pattern: Bash\n"
                "  - type: allow\n"
                "    pattern: Read\n"
                "  - type: allow\n"
                "    pattern: Glob\n"
                "---\n"
            )

            psr = PathScopedRules()
            results = psr.on_directory_accessed(root)
            assert len(results) == 1
            assert len(results[0].rules) == 3

    def test_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            psr = PathScopedRules()
            assert not psr.is_loaded(root)
            psr.on_directory_accessed(root)
            assert psr.is_loaded(root)

    def test_file_without_frontmatter_rules(self):
        """Rule file with no frontmatter rules is still loaded as content only."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rules_dir = root / ".d2c" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "notes.md").write_text("# Notes\nJust some notes, no rules.")

            psr = PathScopedRules()
            results = psr.on_directory_accessed(root)
            assert len(results) == 1
            assert results[0].rules == []
            assert "Just some notes" in (results[0].content or "")


# ── Permission engine integration tests ────────────────────────────────

class TestPermissionEnginePathRules:
    def test_add_rules_dynamically(self):
        engine = PermissionEngine(mode=PermissionMode.DEFAULT)
        assert len(engine.rules) == 0

        engine.add_rules([
            PermissionRule(rule_type=RuleType.DENY, pattern="Bash", reason="No shell"),
        ])
        assert len(engine.rules) == 1

        result = engine.evaluate(PermissionRequest(
            tool_name="Bash", tool_input={"command": "rm -rf /"},
            tool_category=PermissionCategory.SHELL,
        ))
        assert result.decision.name == "DENY"

    def test_dynamic_rules_take_effect_immediately(self):
        engine = PermissionEngine(mode=PermissionMode.DEFAULT)

        # Before adding rule: Bash is ASK (default mode)
        req = PermissionRequest(
            tool_name="Bash", tool_input={},
            tool_category=PermissionCategory.SHELL,
        )
        result_before = engine.evaluate(req)
        assert result_before.decision.name == "ASK"

        # Add deny rule
        engine.add_rules([
            PermissionRule(rule_type=RuleType.DENY, pattern="Bash", reason="Blocked"),
        ])

        # After: Bash is DENY
        result_after = engine.evaluate(req)
        assert result_after.decision.name == "DENY"

    def test_set_path_rules(self):
        engine = PermissionEngine(mode=PermissionMode.DEFAULT)
        psr = PathScopedRules()
        engine.set_path_rules(psr)
        assert engine._path_rules is psr

        engine.set_path_rules(None)
        assert engine._path_rules is None


# ── LazyMemoryLoader integration tests ─────────────────────────────────

class TestLazyMemoryLoaderWithPathRules:
    def test_loads_path_rules_on_file_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subdir = root / "src"
            subdir.mkdir()

            rules_dir = subdir / ".d2c" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "security.md").write_text(
                "---\nrules:\n  - type: deny\n    pattern: Bash\n---\n# Notes\n"
            )

            psr = PathScopedRules()
            loader = LazyMemoryLoader(cwd=root, path_rules=psr)

            content = loader.on_file_accessed(subdir / "main.py")
            # Memory content may have loaded CLAUDE.md (none), and rules loaded
            assert psr.is_loaded(subdir)

            # Verify rules were loaded
            rules = psr.get_rules_for_path(subdir / "main.py")
            assert len(rules) >= 1
            assert rules[0].pattern == "Bash"

    def test_no_path_rules_passed(self):
        """LazyMemoryLoader works fine without PathScopedRules."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subdir = root / "src"
            subdir.mkdir()

            loader = LazyMemoryLoader(cwd=root, path_rules=None)
            content = loader.on_file_accessed(subdir / "file.py")
            assert content is None  # No CLAUDE.md to load, no rules
