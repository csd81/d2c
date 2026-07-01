"""Tool pool assembly — the single source of truth for combining tools.

Pipeline: enumerate → is_enabled() filter → deny-rule pre-filter → return.

Phase 11: MCP tools are discovered and merged into the pool.
MCP tools override built-ins with the same name.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from d2c.tools.agent_tool import AgentTool
from d2c.tools.apply_patch import ApplyPatchTool
from d2c.tools.background_status import BackgroundStatusTool
from d2c.tools.bash_tool import BashTool
from d2c.tools.code_symbols import CodeSymbolsTool
from d2c.tools.config_info import ConfigInfoTool
from d2c.tools.edit_tool import FileEditTool
from d2c.tools.env_info import EnvInfoTool
from d2c.tools.fs_tools import FileInfoTool, ListDirTool
from d2c.tools.git_tools import GitDiffTool, GitStatusTool
from d2c.tools.glob_tool import GlobTool
from d2c.tools.grep_tool import GrepTool
from d2c.tools.memory_tool import MemoryTool
from d2c.tools.notebook_edit import NotebookEditTool
from d2c.tools.package_info import PackageInfoTool
from d2c.tools.read_tool import FileReadTool
from d2c.tools.skill_tool import SkillTool
from d2c.tools.structured_edit import JsonEditTool, ReplaceManyTool
from d2c.tools.task_tools import TaskCreateTool, TaskListTool, TaskUpdateTool
from d2c.tools.tool_search import DeferredToolSchema, ToolSearchTool
from d2c.tools.web_fetch import WebFetchTool
from d2c.tools.web_search import WebSearchTool
from d2c.tools.write_tool import FileWriteTool

if TYPE_CHECKING:
    from d2c.sandbox import SandboxConfig
    from d2c.tools import Tool

logger = logging.getLogger(__name__)


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
    deferred_tools: bool = field(default=False)
    sandbox_config: "SandboxConfig | None" = field(default=None)  # Phase 34

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
        # Read tools
        FileReadTool(),
        GlobTool(),
        GrepTool(),
        # Write tools
        FileWriteTool(),
        FileEditTool(),
        NotebookEditTool(),
        # Shell tools
        BashTool(cwd=config.cwd, sandbox_config=config.sandbox_config),
        # Meta/agent tools
        AgentTool(),
        SkillTool(skills=skills),
        # Web tools
        WebFetchTool(),
        WebSearchTool(),
        # Task tracking
        TaskCreateTool(),
        TaskUpdateTool(),
        TaskListTool(),
        # Tool search (Phase 20: deferred schemas)
        ToolSearchTool(),
        # Phase 34: background subagent status + auto-memory
        BackgroundStatusTool(),
        MemoryTool(),
        # Phase 41: git + filesystem inspection + structured edits
        GitStatusTool(cwd=config.cwd),
        GitDiffTool(cwd=config.cwd),
        ListDirTool(),
        FileInfoTool(),
        ReplaceManyTool(),
        JsonEditTool(),
        # Phase 51: unified-diff patching + env inspection
        ApplyPatchTool(cwd=config.cwd),
        EnvInfoTool(cwd=config.cwd),
        # Phase 56: tool breadth batch 3
        ConfigInfoTool(cwd=config.cwd, permission_mode=config.permission_mode),
        PackageInfoTool(cwd=config.cwd),
        CodeSymbolsTool(),
    ]

    # Filter disabled tools before wrapping (Phase 20 compatibility)
    tools = [t for t in tools if t.is_enabled()]

    # Phase 20: Wrap large-schema tools in DeferredToolSchema
    # when deferred_tools is enabled. Tools with input_schema > 500 chars
    # get abbreviated schemas in initial context, loaded on demand.
    if config.deferred_tools:
        DEFERRED_THRESHOLD = 500
        result: list[Tool] = []
        for t in tools:
            schema_size = len(str(t.input_schema))
            if schema_size > DEFERRED_THRESHOLD and t.name != "ToolSearch":
                # DeferredToolSchema is a duck-typed Tool wrapper (same interface)
                result.append(cast("Tool", DeferredToolSchema(t)))
            else:
                result.append(t)
        return result

    return tools


def filterToolsByDenyRules(tools: list[Tool], rules: list[Rule]) -> list[Tool]:
    return [
        t
        for t in tools
        if not any(r.rule_type == RuleType.DENY and r.matches_tool(t.name) for r in rules)
    ]


async def assembleMCPTools(cwd: Path | None = None) -> list[Tool]:
    """Discover and connect MCP servers, returning MCPTool instances.

    Paper: "Each MCP server is connected at session start; its tools are
    listed and wrapped as MCPTool instances."

    Connection failures are logged but do not prevent session startup.
    """
    from d2c.mcp import MCPTool
    from d2c.mcp.client import MCPClient
    from d2c.mcp.discovery import discover_servers

    servers = discover_servers(cwd)
    mcp_tools: list[Tool] = []

    for server_config in servers:
        try:
            client = MCPClient(server_config)
            await client.connect()
            server_tools = await client.list_tools()

            for tool_def in server_tools:
                mcp_tool = MCPTool(
                    name=tool_def.get("name", "unknown"),
                    description=tool_def.get("description", ""),
                    input_schema=tool_def.get("inputSchema", {"type": "object", "properties": {}}),
                    server_name=server_config.name,
                    server_config=server_config,
                )
                mcp_tools.append(mcp_tool)

            logger.info(
                "MCP server '%s': %d tools discovered",
                server_config.name,
                len(server_tools),
            )
        except Exception as e:
            logger.warning(
                "MCP server '%s' connection failed: %s. Continuing without it.",
                server_config.name,
                e,
            )
            # Try to close partial connection
            try:
                await client.close()
            except Exception:
                pass

    return mcp_tools


async def assembleToolPool(
    config: Config,
    extra_tools: list[Tool] | None = None,
) -> list[Tool]:
    tools = getAllBaseTools(config)
    tools = filterToolsByDenyRules(tools, config.deny_rules)

    # Phase 11: MCP tools — discovered, connected, merged
    # MCP tools override built-ins with the same name (user explicitly configured them)
    try:
        mcp_tools = await assembleMCPTools(config.cwd)
        tools = mcp_tools + tools  # MCP first → overrides built-ins with same name
    except Exception as e:
        logger.warning("MCP tool assembly failed: %s", e)

    if extra_tools:
        extra = filterToolsByDenyRules(extra_tools, config.deny_rules)
        tools.extend(extra)

    seen: dict[str, Tool] = {}
    for t in tools:
        seen[t.name] = t

    deduped = list(seen.values())

    # Phase 20: Give ToolSearchTool access to the full registry
    # so it can search deferred tools and load their schemas.
    for t in deduped:
        if isinstance(t, ToolSearchTool):
            registry: list[DeferredToolSchema | Tool] = list(deduped)
            t.set_registry(registry)
            break

    return deduped
