# Phase 21: Path-Scoped Rules

**Paper Reference:** Section 7.2 — ".d2c/rules/*.md loaded lazily when new directories
are read, potentially changing classifier behavior mid-conversation."

**Priority:** LOW

## Rationale

Path-scoped rules allow different permission and behavior rules for different parts of
the codebase (e.g., stricter rules for auth/, relaxed for docs/). The paper describes
them as part of the lazy-loading CLAUDE.md hierarchy.

## Files to Create/Modify

1. MODIFY `src/d2c/memory.py` — add lazy path-scoped rule loading
2. MODIFY `src/d2c/permissions.py` — dynamic rule updates when path rules load

## Key Design

```python
class PathScopedRules:
    """Rules that apply only when working in specific directories."""
    def __init__(self):
        self._rules: dict[Path, list[Rule]] = {}
        self._loaded_dirs: set[Path] = set()

    def on_file_accessed(self, file_path: Path) -> list[Rule] | None:
        """Called when agent reads a file. Returns new rules if directory
        has path-scoped rules not yet loaded."""
        parent = file_path.resolve().parent
        if parent in self._loaded_dirs:
            return None

        rules_dir = parent / ".d2c" / "rules"
        if not rules_dir.is_dir():
            self._loaded_dirs.add(parent)
            return None

        new_rules = []
        for rule_file in sorted(rules_dir.glob("*.md")):
            frontmatter, body = parse_frontmatter(rule_file.read_text())
            rule = self._parse_rule_file(frontmatter, body)
            new_rules.append(rule)

        self._loaded_dirs.add(parent)
        self._rules[parent] = new_rules
        return new_rules
```

## Integration

Wire into `LazyMemoryLoader.on_file_accessed()` to also load path-scoped rules.
Add loaded rules to PermissionEngine dynamically.

## Edge Cases

- Rules in parent directories apply to all children
- Child directory rules override parent rules for that subtree
- Rules loaded mid-conversation apply immediately to subsequent tool calls
- Circular or conflicting rules → first-match wins

## Tests (~6)

- Path rules loaded when file accessed in directory
- Path rules not loaded twice for same directory
- Child rules don't affect sibling directories
- Path rules integrated into permission engine
- Rules in parent directory apply to children
- Empty rules directory → no error
