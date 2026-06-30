"""Subagent delegation & isolation. Paper Section 8.

Each subagent gets independent context, tool set, and sidechain transcript.
Only the final summary returns to the parent — full history never enters parent context.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

from d2c.tools import PermissionCategory

if TYPE_CHECKING:
    from d2c.tools import Tool
    from d2c.config import Config
    from d2c.hooks import HookRegistry
    from d2c.persistence import SessionStore


# ── Subagent types ────────────────────────────────────────────────────

class SubagentType(Enum):
    EXPLORE = "Explore"
    PLAN = "Plan"
    GENERAL_PURPOSE = "GeneralPurpose"
    CUSTOM = "Custom"


# ── Definition & result ───────────────────────────────────────────────

@dataclass
class SubagentDefinition:
    """Paper Section 8.1: agent definition with configuration."""
    name: str
    description: str
    system_prompt: str
    subagent_type: SubagentType
    tools: list[str] | None = None          # allowlist
    disallowed_tools: list[str] | None = None  # denylist
    model: str | None = None                 # override model
    permission_mode: str | None = None       # override permission mode
    max_turns: int = 25
    background: bool = False


@dataclass
class SubagentResult:
    """Paper Section 8.3: summary-only return to parent."""
    summary: str
    tool_calls: int = 0
    turns: int = 0
    success: bool = True
    sidechain_path: Path | None = None


# ── Tool pool builder ─────────────────────────────────────────────────

def build_subagent_tool_pool(
    definition: SubagentDefinition,
    all_tools: list["Tool"],
) -> list["Tool"]:
    """Build restricted tool subset for a subagent.

    Paper Section 8.2: subagents get a restricted tool set based on
    allowlist/denylist and built-in type restrictions.
    """
    result = list(all_tools)

    # Apply allowlist/denylist
    if definition.tools:
        result = [t for t in result if t.name in definition.tools]
    elif definition.disallowed_tools:
        result = [t for t in result if t.name not in definition.disallowed_tools]

    # Built-in type restrictions
    if definition.subagent_type == SubagentType.EXPLORE:
        # Paper: "primarily read/search-oriented; write and edit in deny-list"
        result = [t for t in result if t.category in (
            PermissionCategory.READ,
            PermissionCategory.META,
        )]

    # Plan agents get standard tools — their job is planning, not execution restriction

    return result


# ── Subagent spawner ──────────────────────────────────────────────────

async def spawn_subagent(
    definition: SubagentDefinition,
    task_prompt: str,
    parent_config: "Config",
    parent_session_store: "SessionStore | None",
    parent_hooks: Any = None,
) -> SubagentResult:
    """Paper Section 8.2-8.3: spawn isolated subagent.

    1. Build isolated tool pool
    2. Create isolated context (no parent history)
    3. Run queryLoop in isolation
    4. Write sidechain transcript
    5. Return ONLY summary to parent
    """
    # Deferred imports to avoid circular dependency
    from d2c.tools.pool import assembleToolPool, Config as PoolConfig
    from d2c.loop import LoopConfig, queryLoop
    from d2c.loop import TextResponse, ToolExecutionEvent, StopEvent
    from d2c.permissions import PermissionEngine, PermissionMode
    from d2c.context import getSystemPrompt, getUserContext, getSystemContext, assembleMessages
    from d2c.persistence import SessionStore, SessionEntry, _utc_now
    from d2c.hooks import HookRegistry, HookEvent, HookDefinition, HookType

    # Build isolated tool pool
    pool_config = PoolConfig(cwd=parent_config.cwd)
    all_tools = await assembleToolPool(pool_config)
    tools = build_subagent_tool_pool(definition, all_tools)

    # Permission mode override
    perm_mode = definition.permission_mode or parent_config.permission_mode or "default"
    perm_engine = PermissionEngine(
        mode=PermissionMode(perm_mode),
        rules=[],
    )

    # Sidechain transcript
    subagent_id = str(uuid.uuid4())[:8]
    if parent_session_store:
        sidechain_store = SessionStore(
            base_dir=parent_session_store.base_dir,
            session_id=subagent_id,
            project_dir=parent_session_store.project_dir,
        )
    else:
        sidechain_store = SessionStore(
            base_dir=parent_config.cwd / ".d2c",
            session_id=subagent_id,
            project_dir=parent_config.cwd,
        )

    # Isolated hooks: SubagentStop instead of Stop
    subagent_hooks = HookRegistry()

    # Phase 15: Fire SubagentStart on parent hooks
    if parent_hooks:
        await parent_hooks.fire(HookEvent.SUBAGENT_START, {
            "subagent_id": subagent_id,
            "subagent_type": definition.subagent_type,
            "task": task_prompt[:500],
        })

    # Build loop config
    loop_config = LoopConfig(
        system_prompt=definition.system_prompt,
        user_context=getUserContext(parent_config),
        model=definition.model or parent_config.model,
        max_turns=definition.max_turns,
        tools=tools,
        permission_engine=perm_engine,
        hooks=subagent_hooks,
        config=parent_config,
        deepseek_api_key=parent_config.deepseek_api_key,
        deepseek_base_url=parent_config.deepseek_base_url,
        session_store=sidechain_store,
    )

    # Assemble context
    system_context = getSystemContext(parent_config)
    full_prompt, messages = assembleMessages(
        loop_config.system_prompt,
        system_context,
        loop_config.user_context,
        [{"role": "user", "content": task_prompt}],
    )
    loop_config.system_prompt = full_prompt

    # Run isolated loop
    final_text = ""
    tool_calls = 0
    turns = 0

    try:
        async for event in queryLoop(loop_config, messages):
            if isinstance(event, ToolExecutionEvent):
                tool_calls += 1
            elif isinstance(event, TextResponse):
                final_text = event.text
            elif isinstance(event, StopEvent):
                turns = getattr(event, 'metadata', {}).get('turns', 0) or turns
    except Exception as e:
        return SubagentResult(
            summary=f"Subagent error: {e}",
            tool_calls=tool_calls,
            turns=turns,
            success=False,
            sidechain_path=sidechain_store.transcript_path,
        )

    return SubagentResult(
        summary=final_text,
        tool_calls=tool_calls,
        turns=turns,
        success=True,
        sidechain_path=sidechain_store.transcript_path,
    )


# ── Subagent definition loader ────────────────────────────────────────

# Built-in subagent system prompts

EXPLORE_AGENT_PROMPT = """You are an Explore agent — read/search-oriented investigation.
You can read files, search code, and gather information.
You CANNOT write, edit, or run shell commands.
Return a concise summary of your findings."""

PLAN_AGENT_PROMPT = """You are a Plan agent — structured planning.
Analyze the task and create a detailed implementation plan.
Identify which files need changes, the order of operations, and potential risks."""

GENERAL_AGENT_PROMPT = """You are a general-purpose subagent.
Complete the assigned task autonomously.
Return a concise summary of what you did and any important findings."""

BUILTIN_SUBAGENTS: dict[str, SubagentDefinition] = {
    "Explore": SubagentDefinition(
        name="Explore",
        description="Read/search-oriented investigation",
        system_prompt=EXPLORE_AGENT_PROMPT,
        subagent_type=SubagentType.EXPLORE,
        disallowed_tools=["Bash", "Write", "Edit"],
    ),
    "Plan": SubagentDefinition(
        name="Plan",
        description="Structured planning",
        system_prompt=PLAN_AGENT_PROMPT,
        subagent_type=SubagentType.PLAN,
    ),
    "General-purpose": SubagentDefinition(
        name="General-purpose",
        description="Broadly capable agent",
        system_prompt=GENERAL_AGENT_PROMPT,
        subagent_type=SubagentType.GENERAL_PURPOSE,
    ),
}


def load_subagent_definition(name: str) -> SubagentDefinition:
    """Load a subagent definition by name.

    Checks built-in types first, then .d2c/agents/*.md files.
    """
    if name in BUILTIN_SUBAGENTS:
        return BUILTIN_SUBAGENTS[name]

    # Check custom definitions in .d2c/agents/*.md
    agents_dir = Path.cwd() / ".d2c" / "agents"
    if agents_dir.is_dir():
        for agent_file in agents_dir.glob("*.md"):
            frontmatter, body = _parse_frontmatter(agent_file.read_text(encoding="utf-8"))
            if frontmatter.get("name") == name:
                return SubagentDefinition(
                    name=name,
                    description=frontmatter.get("description", ""),
                    system_prompt=body,
                    subagent_type=SubagentType.CUSTOM,
                    tools=frontmatter.get("tools"),
                    disallowed_tools=frontmatter.get("disallowedTools"),
                    model=frontmatter.get("model"),
                    permission_mode=frontmatter.get("permissionMode"),
                    max_turns=frontmatter.get("maxTurns", 25),
                    background=frontmatter.get("background", False),
                )

    raise ValueError(f"Unknown subagent type: {name}")


# ── Helpers ───────────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown. Returns (metadata, body)."""
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
