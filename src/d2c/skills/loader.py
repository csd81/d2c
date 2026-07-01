"""Skill definition loading — bundled + user skills. Paper Section 6.1."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillDefinition:
    """A loaded skill with name, description, and full prompt."""

    name: str
    description: str
    prompt: str  # full instruction injected when skill is invoked
    args_schema: dict | None = None  # optional parameter schema
    source: str = "bundled"  # "bundled" | "user"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML-like frontmatter from markdown. Returns (metadata, body)."""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    metadata: dict = {}
    for line in parts[1].strip().split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            elif value.isdigit():
                value = int(value)
            metadata[key] = value

    return metadata, parts[2].strip()


def load_bundled_skills() -> list[SkillDefinition]:
    """Load skills from the bundled skills directory."""
    skills_dir = Path(__file__).parent
    skills: list[SkillDefinition] = []

    for skill_file in sorted(skills_dir.glob("*.md")):
        frontmatter, body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        skills.append(
            SkillDefinition(
                name=skill_file.stem,
                description=frontmatter.get("description", ""),
                prompt=body,
                args_schema=frontmatter.get("args"),
                source="bundled",
            )
        )

    return skills


def load_user_skills(cwd: Path) -> list[SkillDefinition]:
    """Load user-defined skills from .d2c/skills/ directory.

    Phase 46: project-local skills are executable-ish extension surfaces, so
    they are gated on workspace trust here too (defense in depth — not only at
    the load_all_skills entry point).
    """
    from d2c.trust import get_trust_gate

    if not get_trust_gate().is_project_trusted:
        return []

    skills_dir = cwd / ".d2c" / "skills"
    if not skills_dir.is_dir():
        return []

    skills: list[SkillDefinition] = []
    for skill_file in sorted(skills_dir.glob("*.md")):
        frontmatter, body = parse_frontmatter(skill_file.read_text(encoding="utf-8"))
        skills.append(
            SkillDefinition(
                name=skill_file.stem,
                description=frontmatter.get("description", ""),
                prompt=body,
                args_schema=frontmatter.get("args"),
                source="user",
            )
        )

    return skills


def load_all_skills(cwd: Path | None = None) -> list[SkillDefinition]:
    """Load bundled + user skills. User skills override bundled with same name."""
    bundled = load_bundled_skills()

    cwd_resolved = cwd or Path.cwd()
    from d2c.trust import get_trust_gate

    user = load_user_skills(cwd_resolved) if get_trust_gate().is_project_trusted else []

    seen: dict[str, SkillDefinition] = {}
    for s in bundled:
        seen[s.name] = s
    for s in user:
        seen[s.name] = s  # user overrides bundled

    return list(seen.values())
