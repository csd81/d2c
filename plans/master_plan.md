# Python Implementation of Claude Code Architecture

## Context

Based on the paper "Dive into Claude Code" (2604.14228v1.pdf), implement the core agent architecture in Python. The paper describes a ~512K-line TypeScript codebase; we scope to the essential subsystems that demonstrate the architectural principles: agent loop, tool system, permission gating, context management, session persistence, subagents, and extensibility.

## Architecture Overview

```
d2c/                          # project root
├── pyproject.toml
├── src/d2c/                  # main package
│   ├── __init__.py
│   ├── __main__.py           # `python -m d2c` entry
│   ├── main.py               # entry point (CLI dispatch)
│   ├── loop.py               # queryLoop() async generator
│   ├── query_engine.py       # headless/SDK QueryEngine wrapper
│   ├── context.py            # context assembly (system prompt, CLAUDE.md, history)
│   ├── compact.py            # compaction pipeline (budget → auto-compact)
│   ├── permissions.py        # deny-first rules, 4 permission modes
│   ├── persistence.py        # append-only JSONL session transcripts
│   ├── memory.py             # CLAUDE.md 4-level hierarchy + auto memory
│   ├── subagent.py           # subagent delegation & isolation
│   ├── hooks.py              # hook registry & lifecycle events
│   ├── config.py             # settings resolution
│   ├── tools/                # tool implementations
│   │   ├── __init__.py
│   │   ├── base.py           # Tool ABC, ToolResult, ToolUse dataclasses
│   │   ├── pool.py           # assembleToolPool(), filterToolsByDenyRules()
│   │   ├── bash_tool.py      # shell command execution
│   │   ├── read_tool.py      # FileReadTool
│   │   ├── edit_tool.py      # FileEditTool
│   │   ├── write_tool.py     # FileWriteTool
│   │   ├── web_fetch.py      # WebFetchTool
│   │   ├── web_search.py     # WebSearchTool
│   │   ├── skill_tool.py     # SkillTool (meta-tool)
│   │   └── agent_tool.py     # AgentTool (meta-tool, subagent launcher)
│   └── skills/               # bundled skill definitions
│       ├── __init__.py
│       └── loader.py
├── tests/
│   ├── conftest.py
│   ├── test_tools.py
│   ├── test_loop.py
│   ├── test_permissions.py
│   ├── test_persistence.py
│   ├── test_compact.py
│   ├── test_memory.py
│   ├── test_hooks.py
│   └── test_subagent.py
└── README.md
```

---

# Phase 1: Tool Base & Built-in Tools

**Goal**: Define the tool abstraction and implement the 4 core tools (Read, Write, Edit, Bash). Everything else in the system depends on tools being well-defined.

**Rationale**: Tools are the agent's only interface to the outside world. The paper emphasizes that the model emits `tool_use` blocks, the harness parses/validates/dispatches them — the model never directly touches filesystem or shell. Getting the Tool ABC right first prevents rework.

## Files to Create

### `src/d2c/tools/base.py`

```python
@dataclass
class ToolResult:
    output: str                              # text content for the model
    attachments: list[dict] = field(default_factory=list)  # images, files
    error: bool = False                       # True if tool execution failed
    metadata: dict = field(default_factory=dict)  # timing, exit_code, etc.

@dataclass
class ToolUse:
    """Parsed tool_use block from the model response."""
    id: str                                  # unique tool_use id from API
    name: str                                # tool name
    input: dict                              # parsed arguments
    timestamp: float = field(default_factory=time.time)

class PermissionCategory(Enum):
    READ = "read"       # no side effects: ReadTool, WebFetchTool, WebSearchTool
    WRITE = "write"     # filesystem mutation: WriteTool, EditTool
    SHELL = "shell"     # arbitrary code execution: BashTool, PowerShellTool
    META = "meta"       # spawns other agents/tools: AgentTool, SkillTool

class Tool(ABC):
    name: str
    description: str
    input_schema: dict           # JSON Schema dict
    category: PermissionCategory
    is_concurrent_safe: bool = False  # True if can run in parallel with others

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

    def is_enabled(self, config: "Config") -> bool:
        """Runtime availability check. Called during pool assembly."""
        return True

    def to_api_format(self) -> dict:
        """Return Anthropic-compatible tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
```

### `src/d2c/tools/pool.py`

```python
async def assembleToolPool(
    config: "Config",
    permission_mode: str,
    deny_rules: list[Rule],
    extra_tools: list[Tool] | None = None,
) -> list[Tool]:
    """
    Single source of truth for combining built-in and extra tools.
    Pipeline: enumerate → is_enabled() filter → deny-rule pre-filter → return.
    """
    tools = getAllBaseTools(config)
    tools = [t for t in tools if t.is_enabled(config)]
    tools = filterToolsByDenyRules(tools, deny_rules)
    if extra_tools:
        tools.extend(filterToolsByDenyRules(extra_tools, deny_rules))
    # Deduplicate by name (extra_tools override built-in with same name)
    seen = {}
    for t in tools:
        seen[t.name] = t
    return list(seen.values())

def getAllBaseTools(config: "Config") -> list[Tool]:
    """Return all built-in tools. 8 always; conditionally add more."""
    tools = [
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        BashTool(),
        WebFetchTool(),
        WebSearchTool(),
        SkillTool(),
        AgentTool(),
    ]
    if config.os == "Windows":
        tools.append(PowerShellTool())
    return tools

def filterToolsByDenyRules(tools: list[Tool], rules: list[Rule]) -> list[Tool]:
    """
    Pre-filter: strip blanket-denied tools from the model's view.
    Uses same matcher as runtime check. Prevents model from even seeing forbidden tools.
    """
    return [t for t in tools if not any(
        r.is_deny and r.matches_tool(t.name) for r in rules
    )]
```

### `src/d2c/tools/read_tool.py`

```python
class FileReadTool(Tool):
    name = "Read"
    category = PermissionCategory.READ
    is_concurrent_safe = True    # read-only, can run in parallel

    async def execute(self, file_path: str, offset: int = 0, limit: int = 2000) -> ToolResult:
        # Absolute path only (paper: "The file_path parameter must be an absolute path")
        # Handle: file not found → error, permission denied → error, binary files → error
        # Support: PDF (via pymupdf), images (via base64), ipynb (parse cells)
        # Output format: cat -n style with line numbers starting at 1
```

### `src/d2c/tools/write_tool.py`

```python
class FileWriteTool(Tool):
    name = "Write"
    category = PermissionCategory.WRITE
    is_concurrent_safe = False    # mutates filesystem

    async def execute(self, file_path: str, content: str) -> ToolResult:
        # Absolute path only
        # OVERWRITES existing files (must Read first to prevent accidents)
        # Fails if file not previously read in this session (safety check)
```

### `src/d2c/tools/edit_tool.py`

```python
class FileEditTool(Tool):
    name = "Edit"
    category = PermissionCategory.WRITE
    is_concurrent_safe = False

    async def execute(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> ToolResult:
        # Exact string match replacement (paper's Edit tool semantics)
        # Fails if old_string not unique (unless replace_all=True)
        # Must have Read the file first in this session
```

### `src/d2c/tools/bash_tool.py`

```python
class BashTool(Tool):
    name = "Bash"
    category = PermissionCategory.SHELL
    is_concurrent_safe = False    # serializes with other writes

    async def execute(self, command: str, timeout: int = 120_000, dangerouslyDisableSandbox: bool = False, run_in_background: bool = False) -> ToolResult:
        # Runs in working directory
        # Background: starts process, returns handle; caller gets notified on completion
        # Timeout: default 2 min, max 10 min
        # Sibling abort: if another Bash tool errors, in-flight commands terminated
        # Exit code in metadata; stderr captured (even on success)
```

### Edge Cases to Handle

| Tool | Edge Case | Behavior |
|---|---|---|
| Read | File not found | Return error ToolResult with clear message |
| Read | Binary/PDF/image file | Detect, handle appropriately (PDF → text, image → base64) |
| Read | File larger than limit | Return truncated + indication of total size |
| Write | Parent dir doesn't exist | Create directories? No — return error, let model mkdir first |
| Write | File not previously read | Return error: "must Read file first" |
| Edit | old_string not unique | Return error with context; model must provide more context |
| Edit | old_string not found | Return error |
| Bash | Command times out | Return error with partial output |
| Bash | Non-zero exit | Return result with error=True, include stderr in output |

### Tests (`tests/test_tools.py`)

- Read existing file returns content with line numbers
- Read nonexistent file returns error
- Write to absolute path, verify content written
- Write fails if file not read first
- Edit with unique match succeeds
- Edit with non-unique match fails with useful error
- Edit with replace_all=True changes all occurrences
- Bash simple command returns stdout
- Bash timeout kills process
- Bash background returns immediately

### Dependencies

- `pymupdf` — PDF reading
- `pydantic` — input validation for tool parameters

---

# Phase 2: Agent Loop & Context Assembly

**Goal**: Implement `queryLoop()` — the core while-loop that calls the model, dispatches tools, and feeds results back. A basic CLI that does single-turn: user prompt → model → tool → result → response.

**Rationale**: This is the "1.6% AI logic" at the center of the architecture (paper Section 4). Everything else plugs into this loop. Getting it working end-to-end validates the tool system from Phase 1.

## Files to Create

### `src/d2c/loop.py`

The core async generator. Paper Section 4 Figure 2, query.ts.

```python
@dataclass
class LoopState:
    """Single mutable state object. Whole-object replacement at each continue site (paper Section 4.1)."""
    messages: list[dict]
    tool_context: dict
    turn_count: int = 0
    output_tokens_recovery_attempts: int = 0
    has_attempted_reactive_compact: bool = False
    stopped: bool = False
    stop_reason: str | None = None

@dataclass
class LoopConfig:
    """Immutable parameters (paper Section 4.1 step 1)."""
    system_prompt: str
    user_context: str
    permission_callback: Callable
    model: str
    max_turns: int = 25
    tools: list[Tool]
    hooks: "HookRegistry"
    config: "Config"

async def queryLoop(
    loop_config: LoopConfig,
    initial_messages: list[dict],
) -> AsyncGenerator[LoopEvent, None]:
    """
    Async generator yielding stream events.
    Pattern from paper: while not stopped { assemble → model → gate → execute → compact }
    """
    state = LoopState(
        messages=list(initial_messages),
        tool_context={},
    )

    while not state.stopped:
        # --- Context shaping (paper Section 4.3) ---
        # 5 shapers apply in order; we implement 2:
        # 1. Budget reduction — cap tool result sizes
        # 2. Auto-compact — model-generated summary (Phase 5)
        messages_for_query = applyContextShapers(state.messages, loop_config)

        # --- Tool pool ---
        tool_schemas = [t.to_api_format() for t in loop_config.tools]

        # --- Model call ---
        try:
            response = await call_model(
                model=loop_config.model,
                system=appendSystemContext(loop_config.system_prompt, loop_config.config),
                messages=messages_for_query,
                tools=tool_schemas,
            )
        except PromptTooLong:
            # Recovery: reactive compact, then retry; fail if already attempted
            if not state.has_attempted_reactive_compact:
                state.messages = await reactiveCompact(state.messages, loop_config)
                state.has_attempted_reactive_compact = True
                continue
            state.stopped = True
            state.stop_reason = "prompt_too_long"
            yield StopEvent(reason="prompt_too_long")
            break

        # --- Check for text-only response (primary stop condition) ---
        tool_uses = extract_tool_uses(response)
        if not tool_uses:
            stop_result = await loop_config.hooks.fire("Stop", response)
            if stop_result.veto:
                # Hook says keep going — inject guidance and continue
                state.messages.append({"role": "user", "content": stop_result.additional_context})
                continue
            state.stopped = True
            state.stop_reason = "model_finished"
            yield TextResponse(text=response.content)
            break

        # --- Tool dispatch (paper Section 4.2) ---
        for result in await dispatchTools(tool_uses, loop_config, state):
            yield result

            # Hook intervention check (paper: hook_stopped_continuation)
            if result.stop_continuation:
                state.stopped = True
                state.stop_reason = "hook_intervention"
                break

        # --- Turn limit ---
        state.turn_count += 1
        if state.turn_count >= loop_config.max_turns:
            state.stopped = True
            state.stop_reason = "max_turns"
            yield StopEvent(reason="max_turns")
```

### Tool Dispatch Detail

```python
def partitionToolCalls(tool_uses: list[ToolUse], tools: list[Tool]) -> list[list[ToolUse]]:
    """
    Partition into concurrent-safe groups.
    Read-only tools can run in parallel; writes serialize.
    (paper Section 4.2: "Read-only operations can execute in parallel,
    while state-modifying operations like shell commands are serialized.")
    """
    partitions = []
    current_group = []
    for tu in tool_uses:
        tool = find_tool(tu.name, tools)
        if tool and tool.is_concurrent_safe:
            current_group.append(tu)
        else:
            if current_group:
                partitions.append(current_group)
                current_group = []
            partitions.append([tu])  # serialized — own group
    if current_group:
        partitions.append(current_group)
    return partitions

async def dispatchTools(tool_uses, loop_config, state) -> AsyncGenerator:
    """
    Execute tools in partitions. Within each partition, tools run concurrently.
    Results emitted in original order (paper: "output order stays the same
    even when tools run in parallel").
    """
    # Sibling abort: if any Bash tool errors, cancel remaining in-flight tools
    abort_signal = asyncio.Event()

    for partition in partitionToolCalls(tool_uses, loop_config.tools):
        if abort_signal.is_set():
            break

        tasks = []
        for tu in partition:
            task = asyncio.create_task(
                executeOneTool(tu, loop_config, state, abort_signal)
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for tu, result in zip(partition, results):
            if isinstance(result, Exception):
                # Convert exception to error ToolResult
                result = ToolResult(output=str(result), error=True)

            # Append to message history
            state.messages.append(tool_result_to_message(tu, result))
            yield ToolExecutionEvent(tool_use=tu, result=result)
```

### `src/d2c/context.py`

```python
# Paper Section 7.1 — Context assembly is a memoized state loader, not a routing hub.

@dataclass
class SystemContext:
    git_status: str | None
    platform: str
    cwd: str
    date: str
    # ... additional environment info

@lru_cache
def getSystemContext(config: "Config") -> SystemContext:
    """Memoized — paper: git status and env cached, not recomputed every turn."""
    return SystemContext(
        git_status=get_git_status(config.cwd),
        platform=sys.platform,
        cwd=str(config.cwd),
        date=datetime.now().strftime("%Y-%m-%d"),
    )

def getSystemPrompt() -> str:
    """Base system prompt. Paper Section 7.1: assembly via asSystemPrompt()."""
    return textwrap.dedent("""\
        You are d2c, an interactive CLI agent that helps users with software tasks.
        Use tools to read files, edit code, and run shell commands.
        ...
    """)

def appendSystemContext(prompt: str, ctx: SystemContext) -> str:
    """Append system context to base prompt. Paper: system context appended, not prepended."""
    ...

def getUserContext(config: "Config") -> str:
    """Load CLAUDE.md hierarchy + current date. Prepended to message array as user-context."""
    # Phase 6 will expand this
    ...

def prependUserContext(messages: list[dict], user_context: str) -> list[dict]:
    """Paper: CLAUDE.md is user-context message, not system-prompt content."""
    return [{"role": "user", "content": user_context}] + messages

def assembleMessages(
    system_prompt: str,
    system_context: SystemContext,
    user_context: str,
    history: list[dict],
) -> tuple[str, list[dict]]:
    """Return (full_system_prompt, messages_for_api)."""
    full_prompt = appendSystemContext(system_prompt, system_context)
    messages = prependUserContext(history, user_context)
    return full_prompt, messages
```

### `src/d2c/main.py` — Basic CLI

```python
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", help="Single-shot prompt")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--max-turns", type=int, default=25)
    args = parser.parse_args()

    config = Config.load()
    tools = await assembleToolPool(config, "default", config.deny_rules)
    hooks = HookRegistry.from_config(config)
    loop_config = LoopConfig(
        system_prompt=getSystemPrompt(),
        user_context=getUserContext(config),
        permission_callback=...,  # Phase 3
        model=args.model,
        max_turns=args.max_turns,
        tools=tools,
        hooks=hooks,
        config=config,
    )

    system_context = getSystemContext(config)
    full_prompt, messages = assembleMessages(
        loop_config.system_prompt,
        system_context,
        loop_config.user_context,
        [{"role": "user", "content": args.prompt or input("> ")}],
    )

    async for event in queryLoop(loop_config, messages):
        if isinstance(event, TextResponse):
            print(event.text)
        elif isinstance(event, ToolExecutionEvent):
            print(f"[tool] {event.tool_use.name}: {event.result.output[:200]}...")
```

### Edge Cases

| Condition | Handling |
|---|---|
| Model returns no tools, just text | Primary stop condition — yield text, break |
| Prompt too long from API | Attempt reactive compact once; if still fails, terminate |
| Max output tokens hit | Retry with escalated limit (up to 3x, per paper) |
| Tool execution raises exception | Catch, convert to error ToolResult, continue loop |
| Hook vetoes stop | Inject guidance, continue loop |
| Max turns reached | Terminate with reason |

### Tests (`tests/test_loop.py`)

- Mock model: returns text → loop stops after 1 turn
- Mock model: returns tool_use → tool executes → result fed back → model called again
- Mock model: returns text on second call → loop stops
- Max turns exceeded → loop stops with reason
- Tool error → converted to result, loop continues
- Concurrent-safe tools run in same partition
- State-modifying tools each get own partition

---

# Phase 3: Permission System

**Goal**: Implement deny-first rule evaluation with 4 permission modes and the full authorization pipeline.

**Rationale**: Paper Section 5. Safety layer. Without this, the agent has unrestricted tool access. Deny-first evaluation means the system defaults to blocking, not allowing. Rules are the deterministic enforcement layer (CLAUDE.md is probabilistic guidance).

## Files to Create

### `src/d2c/permissions.py`

```python
from enum import Enum
from dataclasses import dataclass, field

class PermissionMode(Enum):
    PLAN = "plan"              # model plans; user approves plan before execution
    DEFAULT = "default"        # most operations need user approval
    ACCEPT_EDITS = "acceptEdits"  # edits + fs ops auto-approved; shell needs approval
    DONT_ASK = "dontAsk"       # no prompts; deny rules still enforced

class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"                # escalate to user

class RuleType(Enum):
    DENY = "deny"
    ALLOW = "allow"

@dataclass
class Rule:
    """Paper: permission rules match by tool name patterns."""
    rule_type: RuleType
    pattern: str      # glob-like: "bash:*", "mcp__*", "Read", "Write:*.env"
    reason: str = ""

    def matches(self, tool_name: str, tool_input: dict | None = None) -> bool:
        """Match tool name against pattern. Supports wildcard *."""
        ...

@dataclass
class PermissionRequest:
    tool_name: str
    tool_input: dict
    tool_category: PermissionCategory
    session_id: str

@dataclass
class PermissionResult:
    decision: PermissionDecision
    reason: str = ""
    modified_input: dict | None = None   # from PreToolUse hook

class PermissionEngine:
    """
    Deny-first rule evaluation (paper Section 5.1).
    
    Core invariant: DENY rules ALWAYS win, even under dontAsk mode.
    Paper: "Deny rules always win, even under looser modes."
    """

    def __init__(self, mode: PermissionMode, rules: list[Rule]):
        self.mode = mode
        self.rules = rules

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        # --- Step 1: Deny rules first (ALWAYS) ---
        for rule in self.rules:
            if rule.rule_type == RuleType.DENY and rule.matches(request.tool_name, request.tool_input):
                return PermissionResult(PermissionDecision.DENY, reason=rule.reason or "Denied by rule")

        # --- Step 2: Allow rules ---
        for rule in self.rules:
            if rule.rule_type == RuleType.ALLOW and rule.matches(request.tool_name, request.tool_input):
                return PermissionResult(PermissionDecision.ALLOW, reason=rule.reason or "Allowed by rule")

        # --- Step 3: Mode-based defaults ---
        return self._mode_default(request)

    def _mode_default(self, request: PermissionRequest) -> PermissionResult:
        if self.mode == PermissionMode.DONT_ASK:
            return PermissionResult(PermissionDecision.ALLOW)
        if self.mode == PermissionMode.PLAN:
            return PermissionResult(PermissionDecision.ASK, reason="Plan mode: awaiting plan approval")
        if self.mode == PermissionMode.ACCEPT_EDITS:
            # Auto-approve: edits + certain fs shell commands (mkdir, rmdir, touch, rm, mv, cp, sed)
            # Must still ASK for: arbitrary shell commands, web fetch (external)
            if request.tool_category == PermissionCategory.READ:
                return PermissionResult(PermissionDecision.ALLOW)
            if request.tool_category == PermissionCategory.WRITE:
                return PermissionResult(PermissionDecision.ALLOW)
            if request.tool_category == PermissionCategory.SHELL:
                return self._check_safe_shell(request)
            if request.tool_category == PermissionCategory.META:
                return PermissionResult(PermissionDecision.ASK)
        # DEFAULT: ask for everything not explicitly allowed
        return PermissionResult(PermissionDecision.ASK)

    def _check_safe_shell(self, request: PermissionRequest) -> PermissionResult:
        """Paper Section 5: acceptEdits auto-approves mkdir, rmdir, touch, rm, mv, cp, sed."""
        SAFE_COMMANDS = ["mkdir", "rmdir", "touch", "rm", "mv", "cp", "sed", "ls", "cat"]
        cmd = request.tool_input.get("command", "")
        first_word = cmd.strip().split()[0] if cmd.strip() else ""
        if first_word in SAFE_COMMANDS:
            return PermissionResult(PermissionDecision.ALLOW)
        return PermissionResult(PermissionDecision.ASK)
```

### Authorization Pipeline (paper Section 5.2)

```python
async def authorize(
    request: PermissionRequest,
    engine: PermissionEngine,
    hooks: "HookRegistry",
    config: "Config",
    interactive_callback: Callable | None = None,
) -> PermissionResult:
    """
    Full pipeline matching paper Section 5.2:
    PreToolUse hook → deny-first rules → permission handler.
    """

    # Stage 1: PreToolUse hook (paper: can return deny/ask or updatedInput)
    hook_result = await hooks.fire("PreToolUse", request)
    if hook_result.decision in (PermissionDecision.DENY, PermissionDecision.ASK):
        return PermissionResult(hook_result.decision, reason="hook decision")
    if hook_result.updated_input:
        request = replace(request, tool_input=hook_result.updated_input)

    # Stage 2: Deny-first rule evaluation
    result = engine.evaluate(request)

    # Stage 3: If ASK and interactive, prompt user
    if result.decision == PermissionDecision.ASK and interactive_callback:
        user_decision = await interactive_callback(request)
        result = user_decision

    # Stage 4: If DENY, fire PermissionDenied hook for retry guidance
    if result.decision == PermissionDecision.DENY:
        await hooks.fire("PermissionDenied", request, result)

    return result
```

### Interactive Permission Handler

```python
async def interactivePermissionCallback(request: PermissionRequest) -> PermissionResult:
    """Paper Section 5.2: standard user approval dialog."""
    print(f"\n  Tool: {request.tool_name}")
    print(f"  Input: {json.dumps(request.tool_input, indent=2)[:500]}")
    print(f"  Category: {request.tool_category.value}")

    while True:
        choice = input("  Allow? [y]es / [n]o / [a]lways allow this tool: ").strip().lower()
        if choice in ("y", "yes", ""):
            return PermissionResult(PermissionDecision.ALLOW)
        elif choice in ("n", "no"):
            return PermissionResult(PermissionDecision.DENY, reason="user denied")
        elif choice in ("a", "always"):
            # Create persistent allow rule
            ...
```

### Integration into queryLoop

In `loop.py`, wrap each tool execution with the permission gate:

```python
async def executeOneTool(tu, loop_config, state, abort_signal):
    request = PermissionRequest(
        tool_name=tu.name,
        tool_input=tu.input,
        tool_category=tool.category,
        session_id=state.session_id,
    )
    result = await authorize(
        request,
        loop_config.permission_engine,
        loop_config.hooks,
        loop_config.config,
        interactive_callback=loop_config.interactive_callback,
    )
    if result.decision == PermissionDecision.DENY:
        # Paper: denial is a routing signal, not a hard stop
        # Model receives denial reason, revises approach in next iteration
        return ToolResult(
            output=f"Permission denied: {result.reason}",
            error=True,
            metadata={"denied": True},
        )
    if result.decision == PermissionDecision.ASK:
        return ToolResult(
            output="Waiting for user approval...",
            metadata={"awaiting_approval": True},
        )
    # ALLOW — proceed to actual execution
    tool = find_tool(tu.name, loop_config.tools)
    return await tool.execute(**result.modified_input or tu.input)
```

### Configuration Format

```yaml
# .d2c/config.yaml
permission_mode: default

permission_rules:
  - type: deny
    pattern: "bash:rm -rf *"
    reason: "Destructive recursive delete is never allowed"
  - type: deny
    pattern: "Write:*.env"
    reason: "Never write to .env files"
  - type: allow
    pattern: "Read"
    reason: "Reading files is always safe"
  - type: allow
    pattern: "Glob"
    reason: "File search is read-only"
```

### Edge Cases

| Condition | Handling |
|---|---|
| Deny rule matches under dontAsk | Still DENY — deny always wins |
| No rules match under default | ASK user |
| acceptEdits + dangerous shell command | ASK (not auto-approved) |
| PreToolUse hook returns updatedInput | Modified input passed through to tool |
| User denies | Denial reason fed back to model as routing signal |
| PermissionDenied hook fires | Retry guidance injected into context |

### Tests (`tests/test_permissions.py`)

- Deny rule blocks matching tool regardless of mode
- Allow rule permits matching tool
- No matching rule → ASK (default mode)
- dontAsk auto-allows when no deny rule matches
- deny still wins under dontAsk
- acceptEdits auto-approves edit tools
- acceptEdits asks for arbitrary shell commands
- acceptEdits auto-approves safe shell commands (mkdir, ls, etc.)
- PreToolUse hook can deny
- PreToolUse hook can modify input
- PermissionDenied hook fires on denial

---

# Phase 4: Session Persistence

**Goal**: Implement append-only JSONL transcripts + resume/fork. Paper Section 9.

**Rationale**: Without persistence, every session starts from scratch. The paper's key design choice: append-only JSONL for auditability, permissions NOT restored on resume.

## Files to Create

### `src/d2c/persistence.py`

```python
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field

SESSION_DIR = ".d2c/sessions"

@dataclass
class SessionEntry:
    """One line in the JSONL transcript."""
    role: str           # "user" | "assistant" | "tool"
    content: str | list[dict]  # text or content blocks
    timestamp: str      # ISO 8601
    entry_type: str     # "message" | "compact_boundary" | "subagent_summary"
    metadata: dict = field(default_factory=dict)

    def to_jsonl_line(self) -> str:
        return json.dumps(dataclasses.asdict(self))

@dataclass
class SessionManifest:
    """Lightweight index for session listing."""
    session_id: str
    created_at: str
    last_updated: str
    cwd: str
    entry_count: int
    compact_boundaries: list[int]  # line numbers of compact markers

class SessionStore:
    """
    Append-only JSONL session transcripts (paper Section 9).
    
    Key properties:
    - Every event is append-only (except compaction cleanup rewrites)
    - Human-readable, version-controllable, reconstructable
    - Resume and fork restore messages but NOT permissions
    """

    def __init__(self, base_dir: Path, session_id: str, project_dir: Path):
        self.base_dir = base_dir
        self.session_id = session_id
        self.project_dir = project_dir  # paper: "transcript path must use same project directory"
        self.transcript_path = base_dir / SESSION_DIR / f"{session_id}.jsonl"
        self.manifest_path = base_dir / SESSION_DIR / f"{session_id}.manifest.json"
        self.sidechain_dir = base_dir / SESSION_DIR / f"{session_id}_sidechains"

    def append(self, entry: SessionEntry) -> None:
        """Append one line to transcript. Thread-safe via file locking."""
        self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write(entry.to_jsonl_line() + "\n")

    def append_compact_boundary(self, preserved_head_uuid: str) -> None:
        """
        Paper Section 9: compact_boundary marker records headUuid
        of the preserved segment, for reconstruction during resume.
        """
        entry = SessionEntry(
            role="system",
            content="",
            timestamp=datetime.now(timezone.utc).isoformat(),
            entry_type="compact_boundary",
            metadata={"preserved_head_uuid": preserved_head_uuid},
        )
        self.append(entry)

    def read_transcript(self) -> list[SessionEntry]:
        """Read all entries for resume/replay."""
        if not self.transcript_path.exists():
            return []
        entries = []
        with open(self.transcript_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entries.append(SessionEntry(**json.loads(line)))
        return entries

    def reconstruct_messages(self, entries: list[SessionEntry]) -> list[dict]:
        """
        Rebuild message array from transcript entries.
        Paper: compact_boundary entries mark where compaction occurred;
        the post-compact messages replace the pre-compact ones.
        """
        messages = []
        in_compacted_region = False
        compact_start = None

        for i, entry in enumerate(entries):
            if entry.entry_type == "compact_boundary":
                in_compacted_region = True
                compact_start = i
                # Skip pre-compact messages; post-compact messages follow
                messages = []  # reset; post-compact messages will follow
                continue
            if entry.entry_type == "message":
                messages.append({
                    "role": entry.role,
                    "content": entry.content,
                })
        return messages

    def get_sidechain_path(self, subagent_id: str) -> Path:
        """Paper Section 8.3: sidechain transcripts for subagents."""
        self.sidechain_dir.mkdir(parents=True, exist_ok=True)
        return self.sidechain_dir / f"{subagent_id}.jsonl"
```

### Session Manager

```python
class SessionManager:
    """
    Session identity: pairs sessionId with sessionProjectDir (paper Section 9.1).
    """

    def create_session(self, cwd: Path) -> SessionStore:
        session_id = str(uuid.uuid4())[:8]
        store = SessionStore(Path.home() / ".d2c", session_id, cwd)
        store.append(SessionEntry(
            role="system",
            content="",
            timestamp=datetime.now(timezone.utc).isoformat(),
            entry_type="message",
            metadata={"event": "session_start", "cwd": str(cwd)},
        ))
        return store

    def resume_session(self, session_id: str, cwd: Path) -> tuple[SessionStore, list[dict]]:
        """
        Paper Section 9.2: --resume rebuilds conversation from transcript.
        IMPORTANT: session-scoped permissions are NOT restored.
        """
        store = SessionStore(Path.home() / ".d2c", session_id, cwd)
        entries = store.read_transcript()
        messages = store.reconstruct_messages(entries)
        return store, messages

    def fork_session(self, source_id: str, cwd: Path) -> SessionStore:
        """
        Paper Section 9.2: fork creates a new session from an existing one.
        Copies transcript, creates new session_id.
        """
        source_store = SessionStore(Path.home() / ".d2c", source_id, cwd)
        new_store = self.create_session(cwd)

        # Copy entries from source transcript
        for entry in source_store.read_transcript():
            if entry.entry_type == "compact_boundary":
                continue  # boundary markers don't copy cleanly
            new_store.append(entry)

        new_store.append(SessionEntry(
            role="system",
            content="",
            timestamp=datetime.now(timezone.utc).isoformat(),
            entry_type="message",
            metadata={"event": "forked_from", "source_session": source_id},
        ))
        return new_store
```

### Integration into queryLoop

During the loop, after each significant event:

```python
# After user prompt:
session_store.append(SessionEntry(
    role="user",
    content=prompt,
    timestamp=datetime.now(timezone.utc).isoformat(),
    entry_type="message",
))

# After assistant response (text or tool_use):
session_store.append(SessionEntry(
    role="assistant",
    content=response_content,
    timestamp=datetime.now(timezone.utc).isoformat(),
    entry_type="message",
))

# After each tool result:
session_store.append(SessionEntry(
    role="tool",
    content=tool_result.output,
    timestamp=datetime.now(timezone.utc).isoformat(),
    entry_type="message",
    metadata={"tool_name": tu.name, "tool_id": tu.id},
))
```

### Edge Cases

| Condition | Handling |
|---|---|
| Resume nonexistent session | Error: session not found |
| Fork with active hooks | Hooks must be re-registered; stale hook refs won't exist |
| Concurrent writes to same session | File-level locking (fcntl/msvcrt) |
| Transcript file corruption (partial write) | Skip malformed lines on read; log warning |
| Compact boundary during resume | reconstruct_messages() handles boundary logic |

### Tests (`tests/test_persistence.py`)

- Append entry → file contains one JSONL line
- Read transcript → returns deserialized entries
- reconstruct_messages() converts entries to API message format
- compact_boundary resets message array (pre-compact discarded)
- Resume restores messages but not permissions
- Fork copies transcript to new session
- Sidechain path is subdirectory of session

---

# Phase 5: Compaction Pipeline

**Goal**: Implement the compaction pipeline — budget reduction + auto-compact. Paper Section 7.3.

**Rationale**: The paper's most distinctive context management feature. Context is the binding resource constraint; the graduated compaction pipeline preserves useful information while freeing space.

## Files to Modify/Create

### `src/d2c/compact.py`

```python
@dataclass
class CompactConfig:
    # Paper: per-tool-result budget cap
    tool_result_max_chars: int = 30_000
    # Paper: context pressure threshold (fraction of context window)
    pressure_threshold: float = 0.85
    # Paper: model context window (tokens)
    context_window_tokens: int = 200_000
    # Approximate chars-per-token for estimation
    chars_per_token: float = 3.5

def applyContextShapers(
    messages: list[dict],
    loop_config: "LoopConfig",
) -> list[dict]:
    """
    Paper Section 4.3: 5 context shapers apply in order.
    Our implementation: 2 shapers.
    
    1. Budget reduction — cap individual tool result sizes
    2. Auto-compact — model-generated summary (gated by pressure threshold)
    """
    # Shaper 1: Budget reduction
    messages = applyBudgetReduction(messages, loop_config.compact_config)

    # Shaper 2: Auto-compact (only if still over threshold)
    if estimate_tokens(messages, loop_config.compact_config) > compute_pressure_limit(loop_config):
        messages = autoCompact(messages, loop_config)

    return messages

def applyBudgetReduction(
    messages: list[dict],
    config: CompactConfig,
) -> list[dict]:
    """
    Paper: "Individual tool results are capped at a configurable size,
    preventing a single verbose output from consuming disproportionate context."
    """
    result = []
    for msg in messages:
        if msg["role"] == "tool" and isinstance(msg["content"], str):
            if len(msg["content"]) > config.tool_result_max_chars:
                truncated = msg["content"][:config.tool_result_max_chars]
                truncated += f"\n... [truncated {len(msg['content']) - config.tool_result_max_chars} chars]"
                msg = {**msg, "content": truncated}
        result.append(msg)
    return result

def estimate_tokens(messages: list[dict], config: CompactConfig) -> int:
    """Rough token estimation: total chars / chars_per_token."""
    total_chars = sum(
        len(json.dumps(m)) if isinstance(m, dict) else len(str(m))
        for m in messages
    )
    return int(total_chars / config.chars_per_token)

def compute_pressure_limit(loop_config: "LoopConfig") -> int:
    return int(
        loop_config.compact_config.context_window_tokens *
        loop_config.compact_config.pressure_threshold
    )

async def autoCompact(
    messages: list[dict],
    loop_config: "LoopConfig",
) -> list[dict]:
    """
    Paper Section 4.3 Shaper #5:
    - Fire PreCompact hooks
    - Generate compact prompt
    - Call model to produce compressed summary
    - Build post-compact messages: summary replaces history
    """
    # Fire PreCompact hooks (paper: "Pre-compact hooks fire first,
    # allowing hook-injected custom instructions")
    hook_context = await loop_config.hooks.fire("PreCompact", messages)

    # Build compact prompt
    compact_prompt = getCompactPrompt(messages, hook_context)

    # Call model (paper: compaction reuses prompt cache when feature flag allows)
    compact_model = loop_config.compact_model or loop_config.model
    summary = await call_model(
        model=compact_model,
        system="Summarize the following conversation. Preserve key decisions, errors, and context.",
        messages=[{"role": "user", "content": compact_prompt}],
        tools=[],  # no tools during compaction
    )

    # Build post-compact messages (paper: buildPostCompactMessages in compact.ts)
    post_compact = buildPostCompactMessages(messages, summary.content)

    # Mark compact boundary for persistence (paper Section 9)
    loop_config.session_store.append_compact_boundary(
        preserved_head_uuid=messages[-1].get("id", "") if messages else ""
    )

    return post_compact

def buildPostCompactMessages(
    original_messages: list[dict],
    summary: str,
) -> list[dict]:
    """
    Paper: "The summary feeds into buildPostCompactMessages().
    Post-compact messages consist of the summary + recent messages after the cut point."
    """
    # Keep system messages at the top
    system_msgs = [m for m in original_messages if m["role"] == "system"]

    # Summary becomes a user message (paper: "Summary messages live in the collapse store")
    summary_msg = {
        "role": "user",
        "content": f"[Previous conversation summary]\n{summary}",
    }

    # Keep the last 2 turns of conversation for continuity
    recent = original_messages[-4:]  # roughly 2 turns (user + assistant)

    return system_msgs + [summary_msg] + recent

def getCompactPrompt(messages: list[dict], hook_context: dict) -> str:
    """
    Paper: "creates a summary request using getCompactPrompt()."
    Summarize all but the last 2 turns.
    """
    to_compact = messages[:-4] if len(messages) > 4 else messages
    lines = []
    for msg in to_compact:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = json.dumps(content)
        lines.append(f"[{role}]: {str(content)[:500]}")
    return "\n".join(lines)
```

### Edge Cases

| Condition | Handling |
|---|---|
| Messages under threshold after budget reduction | Skip auto-compact |
| Compaction model fails | Keep original messages; continue with risk of context overflow |
| PreCompact hook returns error | Log warning; proceed with compaction |
| Very short conversation (< 4 messages) | Skip compaction (nothing meaningful to compress) |
| Summary exceeds budget | Second-pass truncation |

### Tests (`tests/test_compact.py`)

- Budget reduction caps long tool output
- Budget reduction preserves short output
- estimate_tokens returns reasonable estimate
- pressure_threshold triggers auto-compact when over limit
- buildPostCompactMessages preserves system + recent messages
- buildPostCompactMessages includes summary
- Compact boundary recorded in session store

---

# Phase 6: Memory System

**Goal**: Implement CLAUDE.md 4-level hierarchy + auto memory. Paper Section 7.2.

**Rationale**: The paper's distinctive approach to persistent instructions — transparent, file-based, version-controllable, with lazy loading for nested directories.

## Files to Create

### `src/d2c/memory.py`

```python
# Paper Section 7.2: 4-level CLAUDE.md hierarchy

class MemoryLevel(Enum):
    MANAGED = 1   # /etc/d2c/CLAUDE.md (or OS equivalent)
    USER = 2      # ~/.d2c/CLAUDE.md
    PROJECT = 3   # CLAUDE.md, .d2c/CLAUDE.md, .d2c/rules/*.md
    LOCAL = 4     # CLAUDE.local.md (gitignored)

@dataclass
class MemoryFile:
    path: Path
    level: MemoryLevel
    content: str
    priority: int   # higher = loaded later = more model attention

def loadClaudeMdHierarchy(cwd: Path) -> str:
    """
    Paper: "File discovery traverses from the current directory up to root,
    checking for all project and local memory files in each directory.
    Files closer to the current directory have higher priority."
    
    Loading order (reverse priority):
    1. Managed memory
    2. User memory  
    3. Project memory (root → cwd)
    4. Local memory (root → cwd)
    """
    files: list[MemoryFile] = []
    priority = 0

    # Level 1: Managed
    managed_paths = [
        Path("/etc/d2c/CLAUDE.md"),
    ]
    for p in managed_paths:
        if p.exists():
            files.append(MemoryFile(p, MemoryLevel.MANAGED, p.read_text(), priority))
            priority += 1

    # Level 2: User
    user_path = Path.home() / ".d2c" / "CLAUDE.md"
    if user_path.exists():
        files.append(MemoryFile(user_path, MemoryLevel.USER, user_path.read_text(), priority))
        priority += 1

    # Level 3 & 4: Project + Local (traverse root → cwd)
    cwd = cwd.resolve()
    root = Path(cwd.anchor)  # e.g., C:\ on Windows

    current = cwd
    dirs_to_check = []
    while current != root.parent:
        dirs_to_check.append(current)
        current = current.parent

    # Reverse: root first, cwd last (higher priority)
    for d in reversed(dirs_to_check):
        # Project memory
        for name in ["CLAUDE.md", ".d2c/CLAUDE.md"]:
            p = d / name
            if p.exists():
                files.append(MemoryFile(p, MemoryLevel.PROJECT, p.read_text(), priority))
                priority += 1
        # Path-scoped rules (paper: .d2c/rules/*.md)
        rules_dir = d / ".d2c" / "rules"
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                files.append(MemoryFile(rule_file, MemoryLevel.PROJECT, rule_file.read_text(), priority))
                priority += 1
        # Local memory (gitignored)
        local_path = d / "CLAUDE.local.md"
        if local_path.exists():
            files.append(MemoryFile(local_path, MemoryLevel.LOCAL, local_path.read_text(), priority))
            priority += 1

    # Assemble: later files have more model attention (appended last)
    return assembleMemoryContent(files)

def assembleMemoryContent(files: list[MemoryFile]) -> str:
    """
    Paper: "Files load in reverse order of priority: later-loaded files
    receive more model attention."
    
    Each file is a section with its relative path as header.
    @include directives are processed (paper Section 7.2).
    """
    sections = []
    processed_includes: set[str] = set()  # prevent circular includes

    for f in sorted(files, key=lambda x: x.priority):
        content = processMemoryFile(f.content, f.path.parent, processed_includes)
        sections.append(f"<!-- {f.level.name}: {f.path} -->\n{content}")

    return "\n\n---\n\n".join(sections)

def processMemoryFile(
    content: str,
    base_dir: Path,
    processed: set[str],
) -> str:
    """
    Paper Section 7.2: @include directive for modular instruction sets.
    Syntax: @path, @./relative, @~/home, @/absolute
    Only in leaf text nodes (not inside code blocks).
    Circular references prevented by tracking processed paths.
    Non-existent files silently ignored.
    """
    lines = content.split("\n")
    result = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue

        if not in_code_block and stripped.startswith("@"):
            # Parse include directive
            include_path = parseIncludePath(stripped, base_dir)
            if include_path and include_path.exists() and str(include_path) not in processed:
                processed.add(str(include_path))
                included = include_path.read_text()
                result.append(processMemoryFile(included, include_path.parent, processed))
            continue

        result.append(line)

    return "\n".join(result)

def parseIncludePath(directive: str, base_dir: Path) -> Path | None:
    """Parse @path, @./relative, @~/home, @/absolute."""
    path_str = directive[1:].strip()  # remove @
    if path_str.startswith("./") or path_str.startswith(".."):
        return base_dir / path_str
    elif path_str.startswith("~/"):
        return Path.home() / path_str[2:]
    elif path_str.startswith("/"):
        return Path(path_str)
    else:
        return base_dir / path_str
```

### Auto Memory (paper Section 7.2)

```python
class AutoMemoryStore:
    """
    Paper: auto-memory entries that Claude writes during conversations.
    Stored as markdown files in ~/.d2c/memory/ with MEMORY.md index.
    
    Memory types from paper:
    - user: role, preferences, knowledge
    - feedback: corrections and confirmations
    - project: ongoing work context
    - reference: pointers to external resources
    """

    MEMORY_DIR = Path.home() / ".d2c" / "memory"
    INDEX_FILE = MEMORY_DIR / "MEMORY.md"

    def save_memory(self, name: str, memory_type: str, description: str, content: str) -> None:
        """Paper: save memory as markdown file with frontmatter."""
        self.MEMORY_DIR.mkdir(parents=True, exist_ok=True)

        filename = f"{memory_type}_{name.lower().replace(' ', '_')}.md"
        filepath = self.MEMORY_DIR / filename

        frontmatter = textwrap.dedent(f"""\
            ---
            name: {name}
            description: {description}
            type: {memory_type}
            ---
        """)

        filepath.write_text(frontmatter + "\n\n" + content, encoding="utf-8")

        # Update index
        self._update_index(name, filename, description)

    def _update_index(self, name: str, filename: str, description: str) -> None:
        """Paper: MEMORY.md is an index file, one line per entry."""
        entry = f"- [{name}]({filename}) — {description}"
        existing = []
        if self.INDEX_FILE.exists():
            existing = self.INDEX_FILE.read_text().splitlines()

        # Replace existing entry or append
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
```

### Lazy Loading (paper Section 7.2)

```python
class LazyMemoryLoader:
    """
    Paper: "nested-directory instruction files and conditional rules
    are loaded lazily when the agent reads files in those directories."
    """

    def __init__(self, cwd: Path):
        self.cwd = cwd
        self._loaded_dirs: set[Path] = set()
        self._base_memory = loadClaudeMdHierarchy(cwd)  # eager: root → cwd

    def on_file_accessed(self, file_path: Path) -> str | None:
        """
        Called when agent reads a file. If the file's directory has
        a CLAUDE.md that wasn't loaded yet, load it now.
        Returns additional memory content or None.
        """
        parent = file_path.resolve().parent

        # Only trigger for directories BELOW cwd (paper: "nested directories below CWD")
        if not str(parent).startswith(str(self.cwd)):
            return None

        # Check if already loaded
        if parent in self._loaded_dirs:
            return None

        # Check for CLAUDE.md in this directory
        claude_md = parent / "CLAUDE.md"
        local_md = parent / "CLAUDE.local.md"
        rules_dir = parent / ".d2c" / "rules"

        content_parts = []
        for p in [claude_md, local_md]:
            if p.exists():
                content_parts.append(p.read_text())

        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                content_parts.append(rule_file.read_text())

        self._loaded_dirs.add(parent)

        if content_parts:
            return "\n\n".join(content_parts)
        return None
```

### Integration

`getUserContext()` in `context.py` uses `LazyMemoryLoader`:
- At session start: load root→cwd CLUADE.md hierarchy eagerly
- On each file read: check for lazy-load triggers in nested directories

### Tests (`tests/test_memory.py`)

- Multi-level CLAUDE.md loading produces correct priority order
- @include directive works for absolute, relative, and home-relative paths
- @include in code block is NOT processed
- Circular @include is prevented
- Auto memory saves file and updates index
- MEMORY.md index stays under 200 lines
- Lazy loader triggers on first file access in a directory
- Lazy loader does NOT trigger for directories above cwd

---

# Phase 7: Hooks

**Goal**: Implement hook registry and 8 core lifecycle events. Paper Section 6.1.

**Rationale**: Hooks are the zero-context-cost extensibility mechanism (paper: "hooks consume zero context"). They allow external programs to intercept and modify the agent's behavior at every lifecycle stage.

## Files to Create

### `src/d2c/hooks.py`

```python
# Paper Section 6.1: 27 hook events. We implement 8 core events.
# Hook types from paper: command (shell), prompt (LLM), http, callback (SDK-only)

from enum import Enum
from dataclasses import dataclass, field

class HookEvent(Enum):
    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PERMISSION_DENIED = "PermissionDenied"
    STOP = "Stop"
    PRE_COMPACT = "PreCompact"
    SUBAGENT_STOP = "SubagentStop"
    # Not implemented (remaining 18 from paper):
    # SessionEnd, Setup, StopFailure, Elicitation, ElicitationResult,
    # SubagentStart, TeammateIdle, TaskCreated, TaskCompleted,
    # PostCompact, InstructionsLoaded, ConfigChange, CwdChanged,
    # FileChanged, WorktreeCreate, WorktreeRemove, PermissionRequest, Notification

class HookType(Enum):
    COMMAND = "command"    # shell command, reads stdin JSON, writes stdout JSON
    PROMPT = "prompt"      # LLM-based hook
    HTTP = "http"          # HTTP webhook
    CALLBACK = "callback"  # SDK/internal only (not persistable)

@dataclass
class HookDefinition:
    """A configured hook from settings.json or plugins."""
    event: HookEvent
    hook_type: HookType
    command: str | None = None         # for COMMAND type
    prompt: str | None = None          # for PROMPT type
    url: str | None = None             # for HTTP type
    callback: Callable | None = None   # for CALLBACK type
    source: str = "settings"           # "settings" | "plugin" | "managed" | "skill"
    timeout_ms: int = 30_000

@dataclass
class HookResult:
    """Result from firing a hook."""
    # For PreToolUse: decision can be allow/deny, updatedInput can modify tool params
    # For Stop: veto=True keeps the loop going
    # For others: additionalContext injected into context
    decision: str | None = None          # "allow" | "deny" | "ask"
    updated_input: dict | None = None    # PreToolUse: modified tool input
    updated_output: str | None = None    # PostToolUse: modified tool output
    additional_context: str | None = None  # injected into conversation
    veto: bool = False                   # Stop/SubagentStop: prevent stopping
    error: str | None = None

class HookRegistry:
    """
    Paper: "Hook sources include settings.json, plugins, and managed policy
    at startup; skill hooks register dynamically on invocation."
    """

    def __init__(self):
        self._hooks: dict[HookEvent, list[HookDefinition]] = {
            event: [] for event in HookEvent
        }

    def register(self, definition: HookDefinition) -> None:
        """Register a hook for an event."""
        self._hooks[definition.event].append(definition)

    def unregister(self, definition: HookDefinition) -> None:
        """Remove a hook."""
        self._hooks[definition.event].remove(definition)

    @classmethod
    def from_config(cls, config: "Config") -> "HookRegistry":
        """Load hooks from settings files and plugins (paper Section 6.1)."""
        registry = cls()
        for hook_config in config.hooks:
            definition = HookDefinition(
                event=HookEvent(hook_config["event"]),
                hook_type=HookType(hook_config["type"]),
                command=hook_config.get("command"),
                prompt=hook_config.get("prompt"),
                url=hook_config.get("url"),
                source="settings",
                timeout_ms=hook_config.get("timeout", 30_000),
            )
            registry.register(definition)
        return registry

    async def fire(self, event: HookEvent, *args, **kwargs) -> HookResult:
        """
        Fire all hooks for an event. Results are merged:
        - If any hook denies → overall deny
        - Updated input/output from first hook that provides one wins
        - Additional context from all hooks is concatenated
        - If any hook vetoes → veto=True
        """
        merged = HookResult()
        for hook in self._hooks[event]:
            try:
                result = await self._execute_hook(hook, *args, **kwargs)
                merged = self._merge_results(merged, result)
            except Exception as e:
                # Hook errors are non-fatal (paper: hooks fail gracefully)
                merged.error = str(e)
        return merged

    async def _execute_hook(self, hook: HookDefinition, *args, **kwargs) -> HookResult:
        if hook.hook_type == HookType.COMMAND:
            return await self._execute_command_hook(hook, *args, **kwargs)
        elif hook.hook_type == HookType.PROMPT:
            return await self._execute_prompt_hook(hook, *args, **kwargs)
        elif hook.hook_type == HookType.HTTP:
            return await self._execute_http_hook(hook, *args, **kwargs)
        elif hook.hook_type == HookType.CALLBACK:
            return await hook.callback(*args, **kwargs)

    async def _execute_command_hook(self, hook: HookDefinition, context: dict) -> HookResult:
        """
        Paper: shell command hooks receive JSON on stdin, return JSON on stdout.
        """
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(hook.command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(context).encode()),
                timeout=hook.timeout_ms / 1000,
            )
            if proc.returncode != 0:
                return HookResult(error=f"Hook failed: {stderr.decode()}")
            return HookResult(**json.loads(stdout))
        except asyncio.TimeoutError:
            proc.kill()
            return HookResult(error="Hook timed out")

    async def _execute_prompt_hook(self, hook: HookDefinition, context: dict) -> HookResult:
        """Paper: LLM prompt hooks. Model evaluates context and returns structured result."""
        response = await call_model(
            model="claude-haiku-4-5-20251001",  # fast/cheap model for hooks
            system=hook.prompt,
            messages=[{"role": "user", "content": json.dumps(context)}],
            tools=[],  # no tools for hook execution
        )
        try:
            return HookResult(**json.loads(response.content))
        except json.JSONDecodeError:
            return HookResult(additional_context=response.content)

    async def _execute_http_hook(self, hook: HookDefinition, context: dict) -> HookResult:
        """Paper: HTTP webhooks."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                hook.url,
                json=context,
                timeout=hook.timeout_ms / 1000,
            )
            response.raise_for_status()
            return HookResult(**response.json())

    def _merge_results(self, a: HookResult, b: HookResult) -> HookResult:
        """Merge: deny wins, first updated_input wins, contexts concatenate."""
        decision = a.decision
        if b.decision == "deny":
            decision = "deny"
        elif b.decision == "allow" and a.decision != "deny":
            decision = "allow"

        return HookResult(
            decision=decision,
            updated_input=b.updated_input or a.updated_input,
            updated_output=b.updated_output or a.updated_output,
            additional_context=self._concat_context(a.additional_context, b.additional_context),
            veto=a.veto or b.veto,
            error=b.error or a.error,
        )

    @staticmethod
    def _concat_context(a: str | None, b: str | None) -> str | None:
        if a and b:
            return f"{a}\n{b}"
        return a or b
```

### Hook Event Schemas (paper: types/hooks.ts)

```python
# Each event has a specific context schema passed to hooks

PRETOOL_USE_SCHEMA = {
    "tool_name": str,
    "tool_input": dict,
    "session_id": str,
}

POST_TOOL_USE_SCHEMA = {
    "tool_name": str,
    "tool_input": dict,
    "tool_result": str,
    "error": bool,
    "session_id": str,
}

STOP_SCHEMA = {
    "response_text": str,
    "turn_count": int,
    "session_id": str,
}
```

### Configuration Format

```yaml
# .d2c/config.yaml hooks section
hooks:
  - event: PreToolUse
    type: command
    command: python .d2c/hooks/audit.py
    timeout: 5000

  - event: Stop
    type: prompt
    prompt: "Check if the response is complete. Return {'veto': true} if more work is needed."

  - event: SessionStart
    type: http
    url: https://internal.example.com/hooks/session-start
```

### Tests (`tests/test_hooks.py`)

- Command hook receives JSON context on stdin
- Command hook result parsed from stdout
- Prompt hook calls model with context
- Multiple hooks merge correctly (deny wins)
- Updated input from PreToolUse hook passed through
- Stop hook veto prevents loop exit
- Hook timeout kills process and returns error
- Hook error is non-fatal (other hooks still fire)

---

# Phase 8: Subagent Delegation

**Goal**: Implement AgentTool that spawns subagents in isolated contexts. Paper Section 8.

**Rationale**: The paper's multi-agent architecture — task delegation with isolation. Each subagent gets independent context, tool set, and sidechain transcript. Only the final summary returns to parent.

## Files to Create

### `src/d2c/subagent.py`

```python
# Paper Section 8: subagent delegation via AgentTool

class SubagentType(Enum):
    EXPLORE = "Explore"         # read/search only; write/edit denied
    PLAN = "Plan"               # structured planning; execution via standard permission
    GENERAL_PURPOSE = "General-purpose"  # broad capability
    CUSTOM = "Custom"           # user-defined via .d2c/agents/*.md

@dataclass
class SubagentDefinition:
    """Paper Section 8.1: agent definition with configuration fields."""
    name: str
    description: str
    system_prompt: str
    subagent_type: SubagentType
    tools: list[str] | None = None              # allowlist
    disallowed_tools: list[str] | None = None    # denylist
    model: str | None = None                     # override model
    permission_mode: str | None = None           # override permission mode
    max_turns: int = 25
    memory_scope: str = "session"               # "session" | "project"
    background: bool = False                     # run in background

@dataclass
class SubagentResult:
    """Paper Section 8.3: summary-only return to parent."""
    summary: str              # final text the subagent produces
    tool_calls: int            # total tool invocations
    turns: int                 # total turns taken
    success: bool              # whether task completed
    sidechain_path: Path       # where full transcript was written

async def spawn_subagent(
    definition: SubagentDefinition,
    task_prompt: str,
    parent_config: "Config",
    parent_hooks: "HookRegistry",
    session_store: "SessionStore",
) -> SubagentResult:
    """
    Paper Section 8.2-8.3:
    1. Build isolated tool pool (allowlist/denylist from definition)
    2. Override permission mode if specified
    3. Create isolated context (no parent history inheritance)
    4. Run queryLoop in isolation
    5. Write sidechain transcript
    6. Return ONLY summary to parent
    """

    # Build isolated tool pool
    tools = await build_subagent_tool_pool(definition, parent_config)

    # Permission override (paper Section 8.2)
    permission_mode = (
        definition.permission_mode
        or parent_config.permission_mode
        or "default"
    )

    # Create isolated session store (sidechain)
    subagent_id = str(uuid.uuid4())[:8]
    sidechain_store = SessionStore(
        base_dir=session_store.base_dir,
        session_id=subagent_id,
        project_dir=session_store.project_dir,
    )

    # Isolated hooks: SubagentStop instead of Stop
    subagent_hooks = HookRegistry()
    subagent_hooks.register(HookDefinition(
        event=HookEvent.SUBAGENT_STOP,
        hook_type=HookType.CALLBACK,
        callback=parent_config.subagent_stop_callback,
        source="runtime",
    ))

    # Build isolated context (paper: "does not inherit the parent's conversation history")
    initial_messages = [
        {"role": "user", "content": task_prompt},
    ]

    # Run loop
    loop_config = LoopConfig(
        system_prompt=definition.system_prompt,
        user_context=getUserContext(parent_config),  # still gets CLAUDE.md
        permission_callback=None,  # non-interactive: rules + hooks only
        model=definition.model or parent_config.model,
        max_turns=definition.max_turns,
        tools=tools,
        hooks=subagent_hooks,
        config=parent_config,
        session_store=sidechain_store,
        permission_engine=PermissionEngine(
            PermissionMode(permission_mode),
            parent_config.permission_rules,
        ),
    )

    final_response = ""
    tool_calls = 0
    turns = 0

    async for event in queryLoop(loop_config, initial_messages):
        if isinstance(event, ToolExecutionEvent):
            tool_calls += 1
        elif isinstance(event, TextResponse):
            final_response = event.text
        elif isinstance(event, StopEvent):
            turns = event.metadata.get("turns", turns)

    return SubagentResult(
        summary=final_response,
        tool_calls=tool_calls,
        turns=turns,
        success=True,
        sidechain_path=sidechain_store.transcript_path,
    )

async def build_subagent_tool_pool(
    definition: SubagentDefinition,
    config: "Config",
) -> list[Tool]:
    """Paper Section 8.2: restricted tool subset."""
    all_tools = getAllBaseTools(config)

    if definition.tools:
        # Allowlist: only these tools
        all_tools = [t for t in all_tools if t.name in definition.tools]
    elif definition.disallowed_tools:
        # Denylist: remove these tools
        all_tools = [t for t in all_tools if t.name not in definition.disallowed_tools]

    # Built-in subagent type restrictions
    if definition.subagent_type == SubagentType.EXPLORE:
        # Paper: "primarily read/search-oriented; write and edit in deny-list"
        all_tools = [t for t in all_tools if t.category in (
            PermissionCategory.READ,
            PermissionCategory.META,
        )]
    elif definition.subagent_type == SubagentType.PLAN:
        # Plan tool gets restricted tool set; plans need approval via normal model
        pass  # plan agents get standard tools; their job is planning, not executing

    return all_tools
```

### AgentTool (`tools/agent_tool.py`)

```python
class AgentTool(Tool):
    """
    Paper Section 8.1: AgentTool sits alongside SkillTool in the base tool pool
    as a meta-tool that dispatches to subagent definitions.
    
    The model invokes Agent with structured input:
    - prompt: what to delegate
    - subagent_type: built-in type or custom name
    - isolation_mode: "default" | "worktree" (not implemented)
    - permission_mode: optional override
    """
    name = "Agent"
    description = "Launch a subagent to handle a task autonomously."
    category = PermissionCategory.META

    async def execute(
        self,
        prompt: str,
        subagent_type: str | None = None,
        permission_mode_override: str | None = None,
        max_turns: int = 25,
        background: bool = False,
    ) -> ToolResult:
        # Resolve subagent definition
        if subagent_type:
            definition = await load_subagent_definition(subagent_type)
        else:
            definition = SubagentDefinition(
                name="general-purpose",
                description="General-purpose subagent",
                system_prompt=DEFAULT_SUBAGENT_PROMPT,
                subagent_type=SubagentType.GENERAL_PURPOSE,
            )

        if permission_mode_override:
            definition.permission_mode = permission_mode_override
        definition.max_turns = max_turns
        definition.background = background

        result = await spawn_subagent(
            definition,
            task_prompt=prompt,
            parent_config=self._config,
            parent_hooks=self._hooks,
            session_store=self._session_store,
        )

        # Paper Section 8.3: "only the subagent's final response text and metadata
        # return to the parent conversation context"
        return ToolResult(
            output=(
                f"[Subagent '{definition.name}' completed]\n"
                f"Turns: {result.turns}, Tool calls: {result.tool_calls}\n\n"
                f"{result.summary}"
            ),
            metadata={
                "subagent_type": definition.name,
                "sidechain_path": str(result.sidechain_path),
                "tool_calls": result.tool_calls,
                "turns": result.turns,
            },
        )
```

### Custom Subagent Definitions (paper Section 8.1)

```python
# Load from .d2c/agents/*.md (markdown with YAML frontmatter)
# Paper: "The markdown body of each file serves as the agent's system prompt,
# and YAML frontmatter specifies configuration fields"

async def load_subagent_definition(name: str) -> SubagentDefinition:
    """
    Check built-in types first, then .d2c/agents/*.md.
    Paper: "users define custom subagents via .d2c/agents/*.md files,
    and plugins contribute agent definitions via loadPluginAgents.ts"
    """
    BUILTIN = {
        "Explore": SubagentDefinition(
            name="Explore", description="Read/search-oriented investigation",
            system_prompt=EXPLORE_AGENT_PROMPT,
            subagent_type=SubagentType.EXPLORE,
            disallowed_tools=["Bash", "Write", "Edit"],
        ),
        "Plan": SubagentDefinition(
            name="Plan", description="Creates structured plans",
            system_prompt=PLAN_AGENT_PROMPT,
            subagent_type=SubagentType.PLAN,
        ),
        "General-purpose": SubagentDefinition(
            name="General-purpose", description="Broadly capable agent",
            system_prompt=GENERAL_AGENT_PROMPT,
            subagent_type=SubagentType.GENERAL_PURPOSE,
        ),
    }

    if name in BUILTIN:
        return BUILTIN[name]

    # Check custom definitions
    agents_dir = Path.cwd() / ".d2c" / "agents"
    if agents_dir.is_dir():
        for agent_file in agents_dir.glob("*.md"):
            frontmatter, body = parse_frontmatter(agent_file.read_text())
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
```

### Edge Cases

| Condition | Handling |
|---|---|
| Subagent model call fails | Return error summary to parent; parent continues |
| Subagent hits max turns | Return partial summary; parent can re-delegate |
| Subagent permission denied | Denial returned to subagent as routing signal |
| Subagent returns empty | Error ToolResult |
| Background subagent | Fire-and-forget; result via notification |
| Custom agent file malformed | Parse error with helpful message |

### Tests (`tests/test_subagent.py`)

- Explore subagent has no write/edit/bash tools
- Subagent result is summary only (verify sidechain exists, parent context doesn't contain full history)
- AgentTool.execute() returns structured result
- Custom agent loaded from .d2c/agents/*.md
- Disallowed tools filtered from subagent tool pool
- Subagent max turns enforced

---

# Phase 9: SkillTool, WebFetch, WebSearch

**Goal**: Implement remaining built-in tools. Paper Section 6.

**Rationale**: These tools extend the agent's action surface beyond filesystem operations. Skills are the low-context-cost extensibility mechanism; WebFetch/WebSearch provide external information access.

## Files to Create

### `src/d2c/skills/loader.py`

```python
# Paper Section 6.1: Skills are "domain-specific instructions
# injected into context at invocation time" with "low context cost."

@dataclass
class SkillDefinition:
    name: str
    description: str
    prompt: str              # full instruction injected when skill is invoked
    args_schema: dict | None = None  # optional parameter schema

def load_bundled_skills() -> list[SkillDefinition]:
    """Load skills from d2c's bundled skills directory."""
    skills_dir = Path(__file__).parent
    skills = []
    for skill_file in skills_dir.glob("*.md"):
        frontmatter, body = parse_frontmatter(skill_file.read_text())
        skills.append(SkillDefinition(
            name=skill_file.stem,
            description=frontmatter.get("description", ""),
            prompt=body,
            args_schema=frontmatter.get("args"),
        ))
    return skills

def load_user_skills(cwd: Path) -> list[SkillDefinition]:
    """Paper: user-defined skills in .d2c/skills/ directory."""
    skills_dir = cwd / ".d2c" / "skills"
    if not skills_dir.is_dir():
        return []
    skills = []
    for skill_file in skills_dir.glob("*.md"):
        frontmatter, body = parse_frontmatter(skill_file.read_text())
        skills.append(SkillDefinition(
            name=skill_file.stem,
            description=frontmatter.get("description", ""),
            prompt=body,
        ))
    return skills
```

### `src/d2c/tools/skill_tool.py`

```python
class SkillTool(Tool):
    """
    Paper Section 6.1: "SkillTool injects instructions into the current
    context window" vs AgentTool which spawns a new isolated one.
    
    Skills are advertised to the model via their descriptions (low context cost);
    the full prompt is loaded only on invocation.
    """
    name = "Skill"
    description = "Execute a skill by name. Skills provide specialized instructions."
    category = PermissionCategory.META
    is_concurrent_safe = True

    def __init__(self, skills: list[SkillDefinition]):
        self._skills = {s.name: s for s in skills}

    async def execute(self, skill: str, args: str | None = None) -> ToolResult:
        if skill not in self._skills:
            available = ", ".join(self._skills.keys())
            return ToolResult(
                output=f"Unknown skill: {skill}. Available skills: {available}",
                error=True,
            )

        definition = self._skills[skill]
        injected = definition.prompt
        if args:
            injected += f"\n\nArguments: {args}"

        return ToolResult(
            output=injected,
            metadata={
                "skill_name": skill,
                "action": "inject_into_context",
            },
        )
```

### `src/d2c/tools/web_fetch.py`

```python
class WebFetchTool(Tool):
    name = "WebFetch"
    category = PermissionCategory.READ
    is_concurrent_safe = True

    async def execute(self, url: str, max_length: int = 10_000) -> ToolResult:
        # Validate URL (paper: no internal URL guessing — model provides URL or fails)
        # Fetch with httpx, handle redirects, timeout
        # Return page content (truncated to max_length)
```

### `src/d2c/tools/web_search.py`

```python
class WebSearchTool(Tool):
    name = "WebSearch"
    category = PermissionCategory.READ
    is_concurrent_safe = True

    async def execute(self, query: str, max_results: int = 5) -> ToolResult:
        # Use a search API backend (configurable)
        # Return structured results: title, URL, snippet
```

### Tests (`tests/test_skills.py`)

- SkillTool returns skill prompt for valid skill name
- SkillTool returns error for unknown skill
- Bundled skills load from package directory
- User skills load from .d2c/skills/

---

# Key Design Decisions (from paper)

- **Model reasons, harness enforces** — 1.6% AI logic / 98.4% infrastructure ratio (paper Section 3.1)
- **Deny rules always win** — even under loosest permission mode (paper Section 5.1)
- **Read-only tools run in parallel; writes serialize** (paper Section 4.2)
- **Append-only transcripts favor auditability over query power** (paper Section 9)
- **Permissions NOT restored on resume** — trust re-established per session (paper Section 9.2)
- **Subagents return summary only** — full history never enters parent context (paper Section 8.3)
- **Context is the binding resource constraint** — all subsystems account for it (paper Section 3.6)

## Model Backend

DeepSeek only — no Anthropic models. DeepSeek supports an Anthropic-compatible API at
`https://api.deepseek.com/anthropic`, so we use the `anthropic` SDK pointed at DeepSeek's
endpoint. Zero format conversion needed — our tool schemas and message format are already
Anthropic-compatible.

Default model: `deepseek-v4-pro`. See `plans/phase10-deepseek.md` for details.

## Dependencies

- `anthropic` — Anthropic SDK pointed at DeepSeek's Anthropic-compatible endpoint
- `pydantic` — tool input validation
- `httpx` — WebFetch/WebSearch
- `pyyaml` — config parsing, frontmatter parsing
- `pymupdf` — PDF reading (Read tool)
- `rich` — terminal output formatting

## Verification

1. **Phase 1**: `python -c "from d2c.tools import *; t = FileReadTool(); print(t.to_api_format())"` — tools instantiate
2. **Phase 2**: `python -m d2c "read the file at pyproject.toml"` — headless single-shot works end-to-end
3. **Phase 3**: `python -m d2c --permission-mode acceptEdits "list all Python files"` — mode override works
4. **Phase 4**: `python -m d2c --resume <id>` — resume from transcript
5. **Phase 5**: Run a long conversation, verify compaction boundaries in transcript
6. **Phase 6**: Create CLAUDE.md files at multiple levels, verify loading order
7. **Phase 7**: Configure a PreToolUse hook that audits tool calls, verify audit log
8. **Phase 8**: Agent delegates to Explore subagent, verify sidechain transcript
9. **Phase 9**: `python -m d2c "use the commit skill"` — skill prompt injected into context
10. **Phase 10**: `DEEPSEEK_API_KEY=sk-... python -m d2c "hello"` — DeepSeek model backend
11. **Full suite**: `pytest tests/` — all test files passing
