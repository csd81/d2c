"""SkillTool — injects skill prompts into context. Paper Section 6.1.

Skills are advertised to the model via their descriptions (low context cost);
the full prompt is loaded only on invocation. This differs from AgentTool
which spawns a new isolated context — SkillTool injects into the current one.
"""

from __future__ import annotations

from typing import Any, ClassVar

from d2c.tools import PermissionCategory, Tool, ToolResult


SKILL_TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "skill": {
            "type": "string",
            "description": "The skill name to invoke (e.g., 'commit', 'review-pr')",
        },
        "args": {
            "type": "string",
            "description": "Optional arguments to pass to the skill",
        },
    },
    "required": ["skill"],
}


class SkillTool(Tool):
    """Paper Section 6.1: injects skill instructions into the current context.

    Skills are the low-context-cost extensibility mechanism. Each skill is
    a markdown file with a description (shown to the model) and a full
    prompt (loaded only on invocation).

    User skills in .d2c/skills/ override bundled skills with the same name.
    """

    name: ClassVar[str] = "Skill"
    description: ClassVar[str] = (
        "Execute a skill by name. Skills provide specialized domain knowledge "
        "and instructions injected into the current context. Available skills: "
        "commit (git commits). Use when you need specialized instructions for "
        "a specific task."
    )
    input_schema: ClassVar[dict[str, Any]] = SKILL_TOOL_INPUT_SCHEMA
    category: ClassVar[PermissionCategory] = PermissionCategory.META
    is_concurrent_safe: ClassVar[bool] = True

    def __init__(self, skills: list | None = None):
        """Initialize with pre-loaded skill definitions.

        Args:
            skills: List of SkillDefinition objects. If None, loaded lazily.
        """
        from d2c.skills.loader import SkillDefinition
        self._skills: dict[str, SkillDefinition] = {}
        if skills:
            for s in skills:
                self._skills[s.name] = s

    def _ensure_loaded(self) -> None:
        """Lazy-load skills if not provided at construction time."""
        if not self._skills:
            from d2c.skills.loader import load_all_skills
            for s in load_all_skills():
                self._skills[s.name] = s

    async def execute(self, skill: str = "", args: str = "", **kwargs: Any) -> ToolResult:
        self._ensure_loaded()

        if not skill or skill not in self._skills:
            available = ", ".join(sorted(self._skills.keys())) if self._skills else "none"
            return ToolResult(
                output=f"Unknown skill: '{skill}'. Available skills: {available}",
                error=True,
            )

        definition = self._skills[skill]
        injected = definition.prompt

        if args:
            injected += f"\n\n**Arguments:** {args}"

        return ToolResult(
            output=injected,
            metadata={
                "skill_name": skill,
                "source": definition.source,
                "action": "inject_into_context",
            },
        )
