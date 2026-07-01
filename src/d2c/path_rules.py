"""Path-scoped permission rules. Paper Section 7.2.

.d2c/rules/*.md files loaded lazily when new directories are read,
potentially changing classifier behavior mid-conversation. Rules in
parent directories apply to children; child rules override parents.

Rule file format (YAML frontmatter + markdown body):
---
rules:
  - type: deny
    pattern: "Bash"
    reason: "No shell access in this directory"
  - type: allow
    pattern: "Read*"
path: "."
---

Optional markdown instructions for the model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from d2c.permissions import PermissionRule, RuleType

logger = logging.getLogger(__name__)


@dataclass
class PathRuleResult:
    """Result from loading path-scoped rules for a directory.

    Contains both structured PermissionRules (for the permission engine)
    and markdown content (for the model's context).
    """

    rules: list[PermissionRule]
    content: str | None  # Markdown body (without frontmatter) for model context
    source_path: Path


def parse_yaml_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown file.

    Returns (frontmatter_dict, body_text).
    Frontmatter is delimited by --- on its own line.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx == -1:
        return {}, text

    frontmatter_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :]).strip()

    # Simple YAML parsing — handles basic dict and list structures
    # without requiring PyYAML dependency
    try:
        import yaml

        frontmatter = yaml.safe_load(frontmatter_text) or {}
    except ImportError:
        frontmatter = _parse_simple_yaml(frontmatter_text)

    return frontmatter, body


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML parser for simple frontmatter structures.

    Handles:
    - key: value
    - key:
        - item1
        - item2
    - key:
        - subkey: subval
    """
    result: dict[str, Any] = {}
    lines = text.split("\n")
    current_key: str | None = None
    current_list: list[Any] = []
    current_dict: dict[str, Any] | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level key: value
        if not line.startswith(" ") and not line.startswith("\t"):
            # Flush previous list/dict
            if current_key and current_list:
                result[current_key] = current_list
                current_list = []

            if ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip()
                if val:
                    result[key] = val
                    current_key = None
                else:
                    current_key = key
                    current_list = []
                    current_dict = None
        elif current_key and stripped.startswith("- "):
            item = stripped[2:].strip()
            if ":" in item:
                sub_key, _, sub_val = item.partition(":")
                if current_dict is None:
                    current_dict = {}
                    current_list.append(current_dict)
                current_dict[sub_key.strip()] = sub_val.strip()
            else:
                current_list.append(item)
                current_dict = None

    if current_key and current_list:
        result[current_key] = current_list

    return result


class PathScopedRules:
    """Lazy-loaded path-scoped rules from .d2c/rules/*.md files.

    Paper: "Rules loaded mid-conversation apply immediately to subsequent
    tool calls. Parent directory rules apply to children; child directory
    rules override parent rules for that subtree."
    """

    def __init__(self):
        self._rules: dict[Path, list[PathRuleResult]] = {}
        self._loaded_dirs: set[Path] = set()

    def on_directory_accessed(self, dir_path: Path) -> list[PathRuleResult]:
        """Load rules for a directory and its unloaded ancestors.

        Called when the agent accesses files in a directory.
        Returns list of PathRuleResult for newly loaded rules.
        Only loads each directory once.
        """
        resolved = dir_path.resolve()
        results: list[PathRuleResult] = []

        # Walk up from dir_path, loading unloaded dirs
        unloaded: list[Path] = []
        current = resolved
        while True:
            if current not in self._loaded_dirs:
                unloaded.append(current)
            # Stop at filesystem root
            parent = current.parent
            if parent == current:
                break
            current = parent

        # Load from root down (so child rules override parents)
        for d in reversed(unloaded):
            self._loaded_dirs.add(d)
            rules_dir = d / ".d2c" / "rules"
            if rules_dir.is_dir():
                for rule_file in sorted(rules_dir.glob("*.md")):
                    try:
                        text = rule_file.read_text(encoding="utf-8")
                        rule_result = self._parse_rule_file(rule_file, text)
                        if rule_result:
                            results.append(rule_result)
                    except OSError:
                        logger.debug("Failed to read rule file: %s", rule_file)
                        continue

        # Store results
        if results:
            self._rules[resolved] = results

        return results

    def _parse_rule_file(self, path: Path, text: str) -> PathRuleResult | None:
        """Parse a single .d2c/rules/*.md file into structured rules."""
        frontmatter, body = parse_yaml_frontmatter(text)

        rules_data = frontmatter.get("rules", [])
        if not isinstance(rules_data, list):
            rules_data = []

        permission_rules: list[PermissionRule] = []
        for rule_def in rules_data:
            if not isinstance(rule_def, dict):
                continue

            rule_type_str = rule_def.get("type", "deny").lower()
            pattern = rule_def.get("pattern", "*")
            reason = rule_def.get("reason", "")

            try:
                rt = RuleType.DENY if rule_type_str == "deny" else RuleType.ALLOW
            except (ValueError, KeyError):
                rt = RuleType.DENY

            permission_rules.append(
                PermissionRule(
                    rule_type=rt,
                    pattern=pattern,
                    reason=reason or f"Path rule from {path.name}",
                )
            )

        if not permission_rules and not body:
            return None

        return PathRuleResult(
            rules=permission_rules,
            content=body or None,
            source_path=path,
        )

    def get_rules_for_path(self, file_path: Path) -> list[PermissionRule]:
        """Get all applicable path-scoped rules for a given file path.

        Collects rules from the file's directory and all ancestor
        directories that have been loaded. Child rules (loaded later
        in the list) override parent rules for conflict resolution.
        """
        resolved = file_path.resolve()
        parent = resolved.parent if resolved.is_file() else resolved

        applicable: list[PermissionRule] = []
        for rules_dir, rule_results in self._rules.items():
            # Check if this rules directory is an ancestor of the file's directory
            try:
                parent.relative_to(rules_dir)
            except ValueError:
                continue
            for rr in rule_results:
                applicable.extend(rr.rules)

        return applicable

    def is_loaded(self, dir_path: Path) -> bool:
        """Check if a directory's path rules have been loaded."""
        return dir_path.resolve() in self._loaded_dirs

    @property
    def loaded_dirs(self) -> set[Path]:
        return set(self._loaded_dirs)
