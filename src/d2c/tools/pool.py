"""Tool pool assembly — the single source of truth for combining tools.

Pipeline: enumerate → is_enabled() filter → deny-rule pre-filter → return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from d2c.tools.agent_tool import AgentTool
from d2c.tools.bash_tool import BashTool
from d2c.tools.edit_tool import FileEditTool
from d2c.tools.read_tool import FileReadTool
from d2c.tools.skill_tool import SkillTool
from d2c.tools.web_fetch import WebFetchTool
from d2c.tools.web_search import WebSearchTool
from d2c.tools.write_tool import FileWriteTool

if TYPE_CHECKING:
    from d2c.tools import Tool


class RuleType(Enum):
    DENY = "deny"
    ALLOW = "allow"


@dataclass
class Rule:
    rule_type: RuleType
    pattern: str
    reason: str = ""

    def matches_tool(self, tool_name: str) -> bool:
        pt = self.pattern
        if pt.endswith(":*"):
            prefix = pt[:-2]
            return tool_name == prefix or tool_name.startswith(prefix + "__")
        if pt.endswith("*"):
            return tool_name.startswith(pt[:-1])
        return tool_name == pt


@dataclass
class Config:
    """Minimal config for Phase 1. Expanded in later phases."""
    cwd: Path = field(default_factory=Path.cwd)
    permission_mode: str = "default"
    permission_rules: list[Rule] = field(default_factory=list)
    deny_rules: list[Rule] = field(default_factory=list)
    os: str = field(default="")

    def __post_init__(self):
        import platform
        if not self.os:
            self.os = platform.system()

    @classmethod
    def load(cls, cwd: Path | None = None) -> "Config":
        return cls(cwd=cwd or Path.cwd())


def getAllBaseTools(config: Config) -> list[Tool]:
    from d2c.skills.loader import load_all_skills
    skills = load_all_skills(config.cwd)
    tools: list[Tool] = [
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        BashTool(cwd=config.cwd),
        AgentTool(),
        SkillTool(skills=skills),
        WebFetchTool(),
        WebSearchTool(),
    ]
    return tools


def filterToolsByDenyRules(tools: list[Tool], rules: list[Rule]) -> list[Tool]:
    return [t for t in tools if not any(
        r.rule_type == RuleType.DENY and r.matches_tool(t.name) for r in rules
    )]


async def assembleToolPool(
    config: Config,
    extra_tools: list[Tool] | None = None,
) -> list[Tool]:
    tools = getAllBaseTools(config)
    tools = [t for t in tools if t.is_enabled()]
    tools = filterToolsByDenyRules(tools, config.deny_rules)

    if extra_tools:
        extra = filterToolsByDenyRules(extra_tools, config.deny_rules)
        tools.extend(extra)

    seen: dict[str, Tool] = {}
    for t in tools:
        seen[t.name] = t
    return list(seen.values())
