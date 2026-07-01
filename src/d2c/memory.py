"""CLAUDE.md 4-level hierarchy + auto memory. Paper Section 7.2.

Loading order (reverse priority — later loaded = more model attention):
  1. Managed memory (/etc/d2c/CLAUDE.md)
  2. User memory (~/.d2c/CLAUDE.md)
  3. Project memory (CLAUDE.md, .d2c/CLAUDE.md, .d2c/rules/*.md) — root → cwd
  4. Local memory (CLAUDE.local.md) — gitignored, root → cwd

Auto memory: persistent file-based entries stored in ~/.d2c/memory/.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from d2c.path_rules import PathScopedRules


# ── Types ────────────────────────────────────────────────────────────


class MemoryLevel(Enum):
    MANAGED = 1  # /etc/d2c/CLAUDE.md
    USER = 2  # ~/.d2c/CLAUDE.md
    PROJECT = 3  # CLAUDE.md, .d2c/CLAUDE.md, .d2c/rules/*.md
    LOCAL = 4  # CLAUDE.local.md (gitignored)


@dataclass
class MemoryFile:
    path: Path
    level: MemoryLevel
    content: str
    priority: int  # higher = loaded later = more model attention


# ── CLAUDE.md hierarchy loader ───────────────────────────────────────


def loadClaudeMdHierarchy(cwd: Path) -> str:
    """Load 4-level CLAUDE.md hierarchy from root to cwd.

    Paper Section 7.2: "File discovery traverses from the current directory
    up to root, checking for all project and local memory files in each
    directory. Files closer to the current directory have higher priority."
    """
    files: list[MemoryFile] = []
    priority = 0
    processed_includes: set[str] = set()

    # Level 1: Managed
    managed_paths = [Path("/etc/d2c/CLAUDE.md")]
    for p in managed_paths:
        if p.exists() and p.is_file():
            content = _read_file_safe(p)
            if content:
                files.append(MemoryFile(p, MemoryLevel.MANAGED, content, priority))
                priority += 1

    # Level 2: User
    user_path = Path.home() / ".d2c" / "CLAUDE.md"
    if user_path.exists() and user_path.is_file():
        content = _read_file_safe(user_path)
        if content:
            files.append(MemoryFile(user_path, MemoryLevel.USER, content, priority))
            priority += 1

    # Level 3 & 4: Project + Local (traverse root → cwd)
    # Only loaded if workspace is trusted
    from d2c.trust import get_trust_gate

    if get_trust_gate().is_project_trusted:
        cwd = cwd.resolve()
        root = Path(cwd.anchor)

        dirs_to_check = _collect_dirs(cwd, root)

        for d in reversed(dirs_to_check):
            # Project memory
            for name in ["CLAUDE.md", ".d2c/CLAUDE.md"]:
                p = d / name
                if p.exists() and p.is_file():
                    content = _read_file_safe(p)
                    if content:
                        files.append(MemoryFile(p, MemoryLevel.PROJECT, content, priority))
                        priority += 1

            # Path-scoped rules (paper: .d2c/rules/*.md)
            rules_dir = d / ".d2c" / "rules"
            if rules_dir.is_dir():
                for rule_file in sorted(rules_dir.glob("*.md")):
                    content = _read_file_safe(rule_file)
                    if content:
                        files.append(MemoryFile(rule_file, MemoryLevel.PROJECT, content, priority))
                        priority += 1

            # Local memory (gitignored)
            local_path = d / "CLAUDE.local.md"
            if local_path.exists() and local_path.is_file():
                content = _read_file_safe(local_path)
                if content:
                    files.append(MemoryFile(local_path, MemoryLevel.LOCAL, content, priority))
                    priority += 1

    return assembleMemoryContent(files, processed_includes)


def assembleMemoryContent(
    files: list[MemoryFile],
    processed_includes: set[str] | None = None,
) -> str:
    """Assemble memory files into a single string.

    Later-loaded files (higher priority) receive more model attention
    because they appear later in the assembled output.
    """
    if processed_includes is None:
        processed_includes = set()

    sections = []
    for f in sorted(files, key=lambda x: x.priority):
        content = processMemoryFile(f.content, f.path.parent, processed_includes)
        sections.append(f"<!-- {f.level.name}: {f.path} -->\n{content}")

    return "\n\n---\n\n".join(sections)


# ── @include directive processing ─────────────────────────────────────


def processMemoryFile(
    content: str,
    base_dir: Path,
    processed: set[str],
) -> str:
    """Process @include directives in memory file content.

    Paper Section 7.2: @include directive for modular instruction sets.
    Syntax: @path, @./relative, @~/home, @/absolute
    Only in leaf text nodes (not inside code blocks).
    Circular references prevented. Non-existent files silently ignored.
    """
    lines = content.split("\n")
    result: list[str] = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # Track code block boundaries
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue

        # Process @include directives (only outside code blocks)
        if not in_code_block and stripped.startswith("@"):
            include_path = parseIncludePath(stripped, base_dir)
            if include_path and include_path.exists() and include_path.is_file():
                resolved = str(include_path.resolve())
                if resolved not in processed:
                    processed.add(resolved)
                    try:
                        included = include_path.read_text(encoding="utf-8")
                        result.append(processMemoryFile(included, include_path.parent, processed))
                    except Exception:
                        pass
            continue

        result.append(line)

    return "\n".join(result)


def parseIncludePath(directive: str, base_dir: Path) -> Path | None:
    """Parse @path, @./relative, @~/home, @/absolute."""
    path_str = directive[1:].strip()
    if not path_str:
        return None
    if path_str.startswith("./") or path_str.startswith("../"):
        return (base_dir / path_str).resolve()
    elif path_str.startswith("~/"):
        return (Path.home() / path_str[2:]).resolve()
    elif path_str.startswith("/"):
        return Path(path_str)
    else:
        return (base_dir / path_str).resolve()


# ── Auto memory ───────────────────────────────────────────────────────


class AutoMemoryStore:
    """Persistent file-based memory. Paper Section 7.2.

    Saves memories as markdown files with YAML frontmatter in ~/.d2c/memory/.
    MEMORY.md serves as an index. Types: user, feedback, project, reference.
    """

    MEMORY_DIR = Path.home() / ".d2c" / "memory"
    INDEX_FILE = MEMORY_DIR / "MEMORY.md"

    def __init__(self):
        self.MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, memory_type: str, description: str, content: str) -> Path:
        """Save a memory entry and update the index."""
        filename = f"{memory_type}_{_sanitize_filename(name)}.md"
        filepath = self.MEMORY_DIR / filename

        frontmatter = f"---\nname: {name}\ndescription: {description}\ntype: {memory_type}\n---\n"
        filepath.write_text(frontmatter + "\n" + content, encoding="utf-8")
        self._update_index(name, filename, description)
        return filepath

    def load(self, name: str) -> str | None:
        """Load a memory by name."""
        if not self.INDEX_FILE.exists():
            return None
        for line in self.INDEX_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"- [{name}]"):
                filename = line.split("](")[1].split(")")[0] if "](" in line else ""
                if filename:
                    filepath = self.MEMORY_DIR / filename
                    if filepath.exists():
                        return filepath.read_text(encoding="utf-8")
        return None

    def delete(self, name: str) -> bool:
        """Delete a memory and update the index."""
        if not self.INDEX_FILE.exists():
            return False
        lines = self.INDEX_FILE.read_text(encoding="utf-8").splitlines()
        new_lines = []
        removed = False
        for line in lines:
            if line.startswith(f"- [{name}]"):
                # Remove the file
                filename = line.split("](")[1].split(")")[0] if "](" in line else ""
                if filename:
                    filepath = self.MEMORY_DIR / filename
                    if filepath.exists():
                        filepath.unlink()
                removed = True
                continue
            new_lines.append(line)
        if removed:
            self.INDEX_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        return removed

    def _update_index(self, name: str, filename: str, description: str) -> None:
        """Add or update an entry in MEMORY.md."""
        entry = f"- [{name}]({filename}) — {description}"
        existing: list[str] = []
        if self.INDEX_FILE.exists():
            existing = self.INDEX_FILE.read_text(encoding="utf-8").splitlines()

        prefix = f"- [{name}]"
        updated = False
        for i, line in enumerate(existing):
            if line.startswith(prefix):
                existing[i] = entry
                updated = True
                break
        if not updated:
            existing.append(entry)

        self.INDEX_FILE.write_text("\n".join(existing) + "\n", encoding="utf-8")


# ── Lazy memory loader ────────────────────────────────────────────────


class LazyMemoryLoader:
    """Paper: nested-directory CLAUDE.md files loaded lazily on file access.

    At session start, root→cwd hierarchy is loaded eagerly.
    When the agent reads a file in a nested directory below cwd,
    that directory's CLAUDE.md/CLAUDE.local.md/.d2c/rules/*.md
    are loaded and returned as additional context.

    Phase 21: Also loads path-scoped permission rules from .d2c/rules/*.md
    files, which can change classifier behavior mid-conversation.
    """

    def __init__(self, cwd: Path, path_rules: "PathScopedRules | None" = None):
        self.cwd = cwd.resolve()
        self._loaded_dirs: set[Path] = set()
        self._loaded_dirs.add(self.cwd)  # root→cwd already loaded eagerly
        self._path_rules = path_rules

    def on_file_accessed(self, file_path: Path) -> str | None:
        """Called when agent reads a file. Loads memory for the file's directory.

        Returns additional memory content or None.
        Only triggers for directories at or below cwd.

        Side effect: Populates PathScopedRules (if configured) with any
        path-scoped permission rules from .d2c/rules/*.md files.
        """
        # Skip project-level lazy loading if workspace is not trusted
        from d2c.trust import get_trust_gate

        if not get_trust_gate().is_project_trusted:
            return None

        parent = file_path.resolve().parent

        # Only trigger for directories at or below cwd
        cwd_str = str(self.cwd)
        parent_str = str(parent)
        if not parent_str.startswith(cwd_str) or parent_str == cwd_str:
            return None

        # Walk up from parent to find first unloaded dir at or below cwd
        unloaded: list[Path] = []
        current = parent
        while current != self.cwd.parent and str(current).startswith(cwd_str):
            if current not in self._loaded_dirs:
                unloaded.append(current)
            current = current.parent

        if not unloaded:
            return None

        # Mark as loaded
        for d in unloaded:
            self._loaded_dirs.add(d)

        # Phase 21: Load path-scoped rules alongside memory content
        if self._path_rules:
            for d in unloaded:
                self._path_rules.on_directory_accessed(d)

        # Load from unloaded dirs (closest to file first = highest priority)
        content_parts: list[str] = []
        for d in unloaded:
            for name in ["CLAUDE.md", ".d2c/CLAUDE.md"]:
                p = d / name
                if p.exists() and p.is_file():
                    text = _read_file_safe(p)
                    if text:
                        content_parts.append(f"<!-- PROJECT: {p} -->\n{text}")

            rules_dir = d / ".d2c" / "rules"
            if rules_dir.is_dir():
                for rule_file in sorted(rules_dir.glob("*.md")):
                    text = _read_file_safe(rule_file)
                    if text:
                        content_parts.append(f"<!-- PROJECT: {rule_file} -->\n{text}")

            local_p = d / "CLAUDE.local.md"
            if local_p.exists() and local_p.is_file():
                text = _read_file_safe(local_p)
                if text:
                    content_parts.append(f"<!-- LOCAL: {local_p} -->\n{text}")

        if content_parts:
            return "\n\n---\n\n".join(content_parts)
        return None


# ── Helpers ───────────────────────────────────────────────────────────


def _read_file_safe(path: Path) -> str | None:
    """Safe file read — returns None on any error."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _collect_dirs(cwd: Path, root: Path) -> list[Path]:
    """Collect directories from root to cwd."""
    dirs: list[Path] = []
    current = cwd
    while current != root.parent:
        dirs.append(current)
        current = current.parent
    return dirs


def _sanitize_filename(name: str) -> str:
    """Convert a memory name to a safe filename."""
    return name.lower().replace(" ", "_").replace("/", "_").replace("\\", "_")
