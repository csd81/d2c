# Phases 11–25: Closing the Gap with the Paper Spec

## Overview

Phases 1-10 implement the core architecture (378 tests). Phases 11-25 address gaps
identified by comparing the implementation against the 46-page "Dive into Claude Code"
paper (2604.14228v1). Ordered by priority: High → Medium → Low.

---

## HIGH PRIORITY

---

### Phase 11: MCP (Model Context Protocol) Integration

**Paper Reference:** Section 6, 3.2 — primary extensibility mechanism for external tools

**Rationale:** MCP is one of 4 extensibility mechanisms. Without it, the agent cannot
connect to external tool servers, which is the primary way third-party tools integrate.

**Files to Create/Modify:**

1. CREATE `src/d2c/mcp/__init__.py`
2. CREATE `src/d2c/mcp/client.py` — MCP client with transport abstraction
3. CREATE `src/d2c/mcp/transports/stdio.py` — stdio transport (subprocess)
4. CREATE `src/d2c/mcp/transports/sse.py` — Server-Sent Events transport
5. CREATE `src/d2c/mcp/transports/http.py` — HTTP transport
6. CREATE `src/d2c/mcp/transports/websocket.py` — WebSocket transport
7. CREATE `src/d2c/mcp/discovery.py` — MCP server discovery from `.d2c/mcp.json` or config
8. MODIFY `src/d2c/tools/pool.py` — merge MCP tools into `assembleToolPool()`

**Key Design:**

```python
@dataclass
class MCPServerConfig:
    name: str
    command: str | None = None      # stdio
    url: str | None = None          # SSE/HTTP/WS
    transport: str = "stdio"        # stdio | sse | http | websocket
    env: dict = field(default_factory=dict)
    timeout_ms: int = 30_000

class MCPClient:
    """Manages connections to MCP servers, discovers tools, handles lifecycle."""
    async def connect(server: MCPServerConfig) -> None
    async def list_tools() -> list[Tool]
    async def call_tool(name: str, arguments: dict) -> ToolResult
    async def close() -> None
```

**Discovery:** Load servers from `.d2c/mcp.json` and `D2C_MCP_SERVERS` env var.
Each MCP server is connected at session start; its tools are listed and wrapped
as `MCPTool` instances, then merged into the tool pool (MCP tools override
built-ins with the same name).

**Tool wrapper:**
```python
class MCPTool(Tool):
    name = server_tool.name           # from MCP server
    description = server_tool.description
    input_schema = server_tool.inputSchema
    category = PermissionCategory.READ  # conservative default; configurable
    is_concurrent_safe = True           # MCP tools assumed safe for parallel

    async def execute(self, **kwargs) -> ToolResult:
        return await mcp_client.call_tool(self.name, kwargs)
```

**Config format (`.d2c/mcp.json`):**
```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed"],
      "transport": "stdio"
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "transport": "stdio",
      "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
    }
  }
}
```

**Edge Cases:**
- Server connection fails at startup → warn, continue without that server's tools
- Server disconnects mid-session → reconnect once, then mark tools unavailable
- MCP tool name conflicts with built-in → MCP tool wins (user explicitly configured it)
- Large number of tools from MCP (100+) → deferred schema loading (Phase 19)

**Tests (~15):**
- stdio transport connects and lists tools
- MCP tool execution calls remote server
- Server disconnect is handled gracefully
- Tool merging: MCP tool overrides built-in of same name
- Empty `.d2c/mcp.json` → no errors
- Server connection failure → warning, session proceeds
- HTTP transport round-trip
- Tool schema is wrapped correctly for Anthropic API format

---

### Phase 12: 3 Missing Compaction Shapers

**Paper Reference:** Section 7.3 — five-layer compaction pipeline

**Rationale:** We only have shapers 1 (budget reduction) and 5 (auto-compact).
The paper's graduated 5-layer pipeline is one of the most architecturally significant
components. Missing shapers 2-4 reduce context management effectiveness.

**Files to Create/Modify:**

1. MODIFY `src/d2c/compact.py` — add three new shapers

**Shaper 2: Snip (`applySnip`)**

Trims oldest messages while preserving system instructions and recent context.
```python
def applySnip(
    messages: list[dict],
    config: CompactConfig,
) -> list[dict]:
    """
    Paper: "snip trims older history."
    Removes oldest non-system messages when over token budget.
    Preserves: system messages, first user message (task), last N messages.
    """
    keep_system = [m for m in messages if m["role"] == "system"]
    keep_recent = messages[-config.snip_keep_last:]
    # Preserve the original task (first user message after system)
    task_msg = None
    for m in messages:
        if m["role"] == "user" and m.get("content"):
            task_msg = m
            break
    if task_msg and task_msg not in keep_recent:
        return keep_system + [task_msg] + keep_recent
    return keep_system + keep_recent
```

**Shaper 3: Microcompact (`applyMicrocompact`)**

Cache-aware compression that avoids invalidating prompt caches during compression.
```python
async def applyMicrocompact(
    messages: list[dict],
    loop_config: "LoopConfig",
) -> list[dict]:
    """
    Paper: "The cache-aware behavior of microcompact adds further opacity,
    as compression decisions are influenced by prompt caching."
    
    Compresses old tool results into brief summaries without breaking
    the Anthropic prompt cache prefix. Groups tool-result pairs into
    summaries at safe cache break points.
    """
    # Identify safe break points (system message boundaries, non-tool messages)
    # Summarize tool result pairs between break points
    # Replace with compact "tool executed: summary" messages
    ...
```

**Shaper 4: Context Collapse (`applyContextCollapse`)**

Read-time projection replacing full history with a model-generated summary.
```python
async def applyContextCollapse(
    messages: list[dict],
    loop_config: "LoopConfig",
) -> list[dict]:
    """
    Paper: "context collapse substitutes messages with a summary
    (described in the source as 'a read-time projection over the REPL's
    full history')."
    
    Unlike auto-compact which replaces history, context collapse
    creates a read-time view. The full transcript on disk is preserved,
    but the model sees only the collapsed view.
    """
    # Segment conversation into logical chunks (by task/topic)
    # Generate per-segment summaries
    # Build projected view: system + summaries + recent messages
    # Full history remains in session_store for audit/resume
    ...
```

**Integration:** Update `applyContextShapers()` to run all 5 shapers in order:
```python
def applyContextShapers(messages, loop_config, hooks):
    # 1. Budget reduction (always)
    messages = applyBudgetReduction(messages, loop_config.compact_config)
    # 2. Snip (when over pressure threshold)
    if over_pressure(messages, loop_config):
        messages = applySnip(messages, loop_config.compact_config)
    # 3. Microcompact (when still over threshold, cache-aware)
    if over_pressure(messages, loop_config):
        messages = await applyMicrocompact(messages, loop_config)
    # 4. Context collapse (when still over threshold)
    if over_pressure(messages, loop_config):
        messages = await applyContextCollapse(messages, loop_config)
    # 5. Auto-compact (last resort, model-generated summary)
    if over_pressure(messages, loop_config):
        messages = await autoCompact(messages, loop_config)
    return messages
```

**Edge Cases:**
- Snip on very short conversations → no-op
- Microcompact with zero tool results → skip
- Context collapse with < 3 turns → skip (nothing meaningful to collapse)
- Collapse preserves hook-injected context
- Cache break detection: respect Anthropic's 1024-token cache break boundaries

**Tests (~15):**
- Snip trims oldest non-system messages
- Snip preserves task message
- Snip preserves last N configurable messages
- Microcompact summarizes tool-result pairs
- Microcompact respects cache break boundaries
- Context collapse produces read-time projection
- Context collapse preserves full transcript on disk
- applyContextShapers runs all 5 in order
- Each shaper no-ops on under-pressure input
- Shaper pipeline short-circuits when pressure relieved

---

### Phase 13: Plugin System

**Paper Reference:** Section 6 — one of 4 extensibility mechanisms

**Rationale:** Plugins contribute hooks, skills, commands, and subagent definitions.
They are loaded at session start and provide zero-code extension points.

**Files to Create/Modify:**

1. CREATE `src/d2c/plugins/__init__.py`
2. CREATE `src/d2c/plugins/loader.py` — plugin discovery and loading
3. CREATE `src/d2c/plugins/manifest.py` — plugin manifest schema
4. MODIFY `src/d2c/main.py` — load plugins at startup
5. MODIFY `src/d2c/hooks.py` — register plugin hooks

**Key Design:**

```python
@dataclass
class PluginManifest:
    name: str
    version: str
    description: str = ""
    hooks: list[dict] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    mcp_servers: list[dict] = field(default_factory=list)

class PluginLoader:
    """Discovers and loads plugins from multiple sources."""
    
    def __init__(self):
        self.sources = [
            BundledPluginsSource(),          # d2c's built-in plugins
            UserPluginsSource(),             # ~/.d2c/plugins/
            ProjectPluginsSource(),          # .d2c/plugins/
        ]
    
    def discover(self) -> list[PluginManifest]:
        """Enumerate all available plugins across sources."""
        
    def load(self, manifest: PluginManifest) -> LoadedPlugin:
        """Load a plugin and register its contributions."""
```

**Plugin sources (paper: "Hook sources include settings.json, plugins, and managed policy"):**
1. Bundled: `src/d2c/plugins/bundled/` — ship with d2c
2. User: `~/.d2c/plugins/` — per-user plugins
3. Project: `.d2c/plugins/` — per-project plugins (highest precedence)

**A plugin is a directory with `manifest.json`:**
```json
{
  "name": "lint-on-save",
  "version": "1.0.0",
  "description": "Runs linter after every file write",
  "hooks": [
    {
      "event": "PostToolUse",
      "type": "command",
      "command": "python .d2c/plugins/lint-on-save/hook.py"
    }
  ],
  "skills": ["commit"],
  "agents": ["code-reviewer"]
}
```

**Integration:** At startup in `main.py`, after config loading:
```python
loader = PluginLoader()
registry = loader.discover_and_load()
# Register hooks into HookRegistry
for manifest, plugin in registry:
    for hook_def in manifest.hooks:
        hook_registry.register(HookDefinition(...))
    # Register skills into SkillTool
    for skill_path in manifest.skills:
        skill_tool.register(load_skill(skill_path))
    # Register agents
    for agent_path in manifest.agents:
        agent_registry.register(load_agent(agent_path))
```

**Edge Cases:**
- Plugin fails to load → warn, continue without it
- Plugin with same name from multiple sources → project wins
- Plugin depends on another plugin → dependency ordering
- Malformed manifest → skip with error message

**Tests (~10):**
- Plugin discovery from bundled directory
- Plugin discovery from user directory
- Plugin precedence: project > user > bundled
- Plugin hooks registered into HookRegistry
- Plugin skills added to SkillTool
- Malformed manifest produces warning, not crash
- Plugin with missing dependency → skip with error
- Plugin hot-reload not supported (load once at startup)

---

## MEDIUM PRIORITY

---

### Phase 14: Remaining Permission Modes (Auto + BypassPermissions)

**Paper Reference:** Section 5 — "up to seven permission modes" (we have 4)

**Rationale:** The paper specifies 7 modes, with Auto being the ML-classifier-based
mode that enables higher autonomy with safety. BypassPermissions is the internal
"trust the operator" mode.

**Files to Create/Modify:**

1. MODIFY `src/d2c/permissions.py` — add Auto, BypassPermissions modes
2. CREATE `src/d2c/permissions/classifier.py` — auto-mode safety classifier

**Auto Mode (PermissionMode.AUTO):**
```python
class AutoClassifier:
    """
    Paper: "two-stage fast-filter and chain-of-thought evaluation of tool safety."
    
    Stage 1 (fast-filter): Heuristic check — is this tool+input combination
    in the known-safe category? (e.g., Read on a text file)
    
    Stage 2 (CoT): Call a fast model (Haiku) with the tool name and input,
    asking it to classify safety. Returns: safe/unsafe/review.
    """

    async def evaluate(self, request: PermissionRequest) -> PermissionResult:
        # Stage 1: Fast heuristics
        fast = self._fast_filter(request)
        if fast is not None:
            return fast
        
        # Stage 2: Chain-of-thought classifier
        return await self._cot_classify(request)

    def _fast_filter(self, request: PermissionRequest) -> PermissionResult | None:
        """Quick heuristic checks. Returns None if needs CoT."""
        # Read + text file + in workspace → SAFE
        if request.tool_category == PermissionCategory.READ:
            if request.tool_name in ("Read", "Glob", "Grep"):
                return PermissionResult(PermissionDecision.ALLOW, reason="safe read")
        # Write to new file in workspace → LIKELY SAFE
        # Shell with ls/cat/echo in workspace → LIKELY SAFE
        # Shell with rm -rf / → UNSAFE (always deny)
        if request.tool_name == "Bash":
            cmd = request.tool_input.get("command", "")
            if cmd.strip().startswith("rm -rf /"):
                return PermissionResult(PermissionDecision.DENY, reason="destructive")
        return None  # Needs CoT evaluation

    async def _cot_classify(self, request: PermissionRequest) -> PermissionResult:
        """Call a fast model for safety classification."""
        ...
```

**BypassPermissions Mode (PermissionMode.BYPASS):**
```python
def _mode_default(self, request):
    if self.mode == PermissionMode.BYPASS:
        # Skip most prompts but safety-critical checks remain
        if self._is_safety_critical(request):
            return PermissionResult(PermissionDecision.ASK, 
                                    reason="safety-critical despite bypass")
        return PermissionResult(PermissionDecision.ALLOW)
```

**Integration:** Update `PermissionEngine.evaluate()` to handle new modes.
Update CLI with `--permission-mode auto` and `--permission-mode bypass`.

**Edge Cases:**
- Auto classifier model unavailable → fall back to default/ask mode
- CoT classifier times out → deny (safety conservative)
- Bypass mode still checks deny rules (deny always wins)
- Fast-filter false positive → CoT stage catches it

**Tests (~12):**
- Auto mode fast-filter approves safe reads
- Auto mode fast-filter denies destructive commands
- Auto mode CoT classifier called for ambiguous cases
- CoT classifier result respected
- Bypass mode auto-approves non-critical operations
- Bypass mode still asks for safety-critical operations
- Bypass mode still enforces deny rules
- Auto classifier timeout → deny
- Auto classifier model error → fallback

---

### Phase 15: Remaining 18 Hook Events

**Paper Reference:** Section 6.1 — 27 hook events (we have 9)

**Rationale:** The 18 missing events cover session lifecycle, subagent lifecycle,
notifications, configuration changes, and elicitation. They enable deeper
extensibility and audit.

**Files to Create/Modify:**

1. MODIFY `src/d2c/hooks.py` — add 18 new HookEvent values and wire them

**New events to add:**

| Event | When Fired | Priority |
|---|---|---|
| `SessionEnd` | Session is ending | Medium |
| `Setup` | After initialization, before first prompt | Medium |
| `StopFailure` | Stop hook itself fails | Medium |
| `Elicitation` | Model requests clarification from user | Medium |
| `ElicitationResult` | User responds to elicitation | Medium |
| `SubagentStart` | Subagent begins execution | Medium |
| `TeammateIdle` | Coordinated agent team member idle | Low |
| `TaskCreated` | New task created in task tracking | Medium |
| `TaskCompleted` | Task marked complete | Medium |
| `PostCompact` | After compaction completes | Low |
| `InstructionsLoaded` | CLAUDE.md loaded | Low |
| `ConfigChange` | Config modified during session | Low |
| `CwdChanged` | Working directory changes | Low |
| `FileChanged` | File modified on disk (external) | Low |
| `WorktreeCreate` | Git worktree created for subagent | Low |
| `WorktreeRemove` | Git worktree removed | Low |
| `PermissionRequest` | Before permission dialog shown | Low |
| `Notification` | Generic notification event | Medium |

**Wiring locations (in `queryLoop` and other modules):**
```python
# SessionEnd — fire in main.py before exit
await hooks.fire(HookEvent.SESSION_END, {"session_id": ..., "turns": ...})

# SubagentStart — fire in subagent.py spawn_subagent()
await parent_hooks.fire(HookEvent.SUBAGENT_START, {
    "subagent_type": definition.subagent_type,
    "task_prompt": task_prompt,
})

# TaskCreated/TaskCompleted — future Phase (when tasks are added)
# Elicitation — when model asks a question (needs model output parsing)
# PostCompact — after autoCompact() returns
await hooks.fire(HookEvent.POST_COMPACT, {
    "pre_count": len(original),
    "post_count": len(post_compact),
})
```

**Tests (~10):**
- SessionEnd fires on session exit
- SubagentStart fires when agent spawned
- Each event's context schema is correct
- Hook errors in new events are non-fatal
- Multiple hooks on same event merge correctly

---

### Phase 16: Worktree Isolation for Subagents

**Paper Reference:** Section 8.2 — "Creates a temporary git worktree, giving the
subagent its own copy of the repository to modify without affecting the parent's
working tree."

**Rationale:** Worktree isolation is the primary filesystem isolation mechanism.
It enables "safe" exploration without contaminating the parent's workspace.

**Files to Create/Modify:**

1. CREATE `src/d2c/worktree.py` — git worktree management
2. MODIFY `src/d2c/subagent.py` — add worktree isolation mode

**Key Design:**

```python
@dataclass
class WorktreeContext:
    worktree_path: Path
    branch_name: str
    original_repo: Path

class WorktreeManager:
    """Manages git worktree lifecycle for subagent isolation."""
    
    def create(self, repo_path: Path, branch_name: str | None = None) -> WorktreeContext:
        """Create a temporary git worktree. Returns context for cleanup."""
        if branch_name is None:
            branch_name = f"d2c-subagent-{uuid4().hex[:8]}"
        worktree_path = repo_path.parent / f".d2c-worktrees/{branch_name}"
        subprocess.run(["git", "worktree", "add", str(worktree_path), "-b", branch_name])
        return WorktreeContext(worktree_path, branch_name, repo_path)
    
    def remove(self, ctx: WorktreeContext) -> None:
        """Remove the worktree and prune the branch."""
        subprocess.run(["git", "worktree", "remove", str(ctx.worktree_path), "--force"])
        subprocess.run(["git", "branch", "-D", ctx.branch_name])
    
    def get_changes(self, ctx: WorktreeContext) -> str:
        """Get the diff of changes made in the worktree."""
        result = subprocess.run(
            ["git", "-C", str(ctx.worktree_path), "diff"],
            capture_output=True, text=True
        )
        return result.stdout
```

**Subagent integration:**
```python
async def spawn_subagent(definition, task_prompt, ..., isolation_mode="default"):
    worktree_ctx = None
    try:
        if isolation_mode == "worktree":
            if not is_git_repo(parent_config.cwd):
                raise ValueError("Worktree isolation requires a git repository")
            worktree_ctx = worktree_manager.create(parent_config.cwd)
            # Override subagent's cwd to worktree path
            subagent_cwd = worktree_ctx.worktree_path
        
        # ... run subagent loop in isolated cwd ...
        
        # If worktree: return diff as part of result
        if worktree_ctx:
            changes = worktree_manager.get_changes(worktree_ctx)
            # Apply changes to parent? Or just report? Paper says "without affecting parent"
            
    finally:
        if worktree_ctx:
            worktree_manager.remove(worktree_ctx)
```

**Edge cases:**
- Not a git repo → error, fall back to in-process isolation
- Worktree creation fails (disk full, permissions) → error
- Subagent modifies files in worktree → diff captured in result
- Cleanup fails → log warning, leave worktree for manual cleanup

**Tests (~8):**
- Worktree creation succeeds in git repo
- Subagent runs in worktree isolation
- Worktree changes don't affect parent repo
- Diff captured correctly
- Cleanup removes worktree
- Non-git repo → error with clear message
- Worktree creation failure → error
- Cleanup failure → warning, no crash

---

### Phase 17: Shell Sandboxing

**Paper Reference:** Section 5 — `shouldUseSandbox.ts`, sandboxed command execution

**Rationale:** The paper mentions sandboxing as a key safety layer that "reduced the
frequency of permission prompts by an estimated 84%." It's a defense-in-depth measure.

**Files to Create/Modify:**

1. CREATE `src/d2c/sandbox.py` — sandbox configuration and detection
2. MODIFY `src/d2c/tools/bash_tool.py` — add sandboxed execution path

**Key Design:**

```python
@dataclass
class SandboxConfig:
    enabled: bool = False
    backend: str = "process"          # process | docker | windows-sandbox
    allowed_dirs: list[Path] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    network_enabled: bool = False
    max_memory_mb: int = 512
    timeout_ms: int = 120_000

class SandboxExecutor:
    """Abstracts sandboxed command execution."""
    
    def should_use_sandbox(self, command: str, config: SandboxConfig) -> bool:
        """Paper: shouldUseSandbox.ts — determines if sandboxing applies."""
        # Check if sandbox is enabled
        # Check if command is safe enough to NOT sandbox (ls, cat, etc.)
        # Check if command is too complex for sandbox
        # Return True if sandbox should wrap the command
        
    async def execute_sandboxed(self, command: str, config: SandboxConfig) -> ToolResult:
        """Execute a command in the sandbox."""
        if config.backend == "process":
            return await self._process_sandbox(command, config)
        elif config.backend == "docker":
            return await self._docker_sandbox(command, config)

    async def _process_sandbox(self, command, config) -> ToolResult:
        """Windows job object / Unix cgroup + seccomp-based sandbox."""
        # On Windows: use Job Objects for resource limits
        # On Unix: use subprocess with restricted environment
        # Enforce: allowed dirs, no network, memory limits, timeout
        ...

    async def _docker_sandbox(self, command, config) -> ToolResult:
        """Docker-based sandbox (optional backend)."""
        ...
```

**Integration in BashTool:**
```python
async def execute(self, command, ..., dangerouslyDisableSandbox=False):
    if sandbox.should_use_sandbox(command) and not dangerouslyDisableSandbox:
        return await sandbox.execute_sandboxed(command)
    else:
        return await self._execute_raw(command)
```

**Config:**
```yaml
# .d2c/config.yaml
sandbox:
  enabled: true
  backend: process
  allowed_dirs: ["."]
  network_enabled: false
```

**Edge Cases:**
- Sandbox not available on platform → warn, fall back to unsandboxed
- Sandbox process killed by OOM → error with memory limit message
- Network-enabled sandbox + sensitive command → ask for permission
- `dangerouslyDisableSandbox` flag → bypass sandbox (requires explicit permission)

**Tests (~10):**
- should_use_sandbox returns True for arbitrary commands
- should_use_sandbox returns False for safe commands (ls, cat)
- Process sandbox restricts file access
- Sandbox timeout kills long-running commands
- Docker sandbox (when Docker available)
- dangerouslyDisableSandbox bypasses sandbox
- Sandbox unavailable → fallback with warning
- Network isolation in sandbox

---

## LOWER PRIORITY

---

### Phase 18: Missing Core Tools (Glob, Grep, NotebookEdit, Task tools)

**Paper Reference:** Section 3.2 — "Up to 54 built-in tools" (we have ~8)

**Rationale:** Several essential developer tools listed in the paper's architecture
are missing. These are high-ROI additions since they're frequently used.

**Files to Create/Modify:**

1. CREATE `src/d2c/tools/glob_tool.py` — File glob pattern matching
2. CREATE `src/d2c/tools/grep_tool.py` — Content search with ripgrep
3. CREATE `src/d2c/tools/notebook_edit.py` — Jupyter notebook manipulation
4. CREATE `src/d2c/tools/task_tools.py` — TaskCreate, TaskUpdate, TaskList, TaskGet
5. CREATE `src/d2c/tools/tool_search.py` — Deferred tool schema loading
6. MODIFY `src/d2c/tools/pool.py` — register new tools

**Glob Tool:**
```python
class GlobTool(Tool):
    name = "Glob"
    description = "Fast file pattern matching. Supports glob patterns like **/*.js."
    category = PermissionCategory.READ
    is_concurrent_safe = True

    async def execute(self, pattern: str, path: str = ".") -> ToolResult:
        # Uses pathlib.glob, sorted by mtime
        # Returns relative file paths
```

**Grep Tool:**
```python
class GrepTool(Tool):
    name = "Grep"
    description = "Content search with ripgrep. Supports full regex syntax."
    category = PermissionCategory.READ
    is_concurrent_safe = True

    async def execute(self, pattern: str, path: str = ".", 
                      glob: str = None, output_mode: str = "files_with_matches",
                      head_limit: int = 250, multiline: bool = False) -> ToolResult:
        # Wraps ripgrep (rg) for content search
        # Modes: content, files_with_matches, count
        # Context lines: -A, -B, -C
```

**NotebookEdit Tool:**
```python
class NotebookEditTool(Tool):
    name = "NotebookEdit"
    description = "Edit Jupyter notebook cells."
    category = PermissionCategory.WRITE
    is_concurrent_safe = False

    async def execute(self, notebook_path: str, cell_id: str = None,
                      new_source: str = None, cell_type: str = None) -> ToolResult:
        # Parse .ipynb JSON, modify cells, write back
```

**Task Tools:**
```python
class TaskCreateTool(Tool):
    name = "TaskCreate"
    description = "Create a structured task for tracking progress."
    category = PermissionCategory.META
    ...

class TaskUpdateTool(Tool):
    name = "TaskUpdate"  
    description = "Update task status (pending → in_progress → completed)."
    category = PermissionCategory.META
    ...

class TaskListTool(Tool):
    name = "TaskList"
    description = "List all current tasks."
    category = PermissionCategory.META
    ...
```

**Tests (~15):**
- Glob finds files matching pattern
- Glob sorts by modification time
- Grep finds content in files
- Grep supports context lines
- Grep multiline mode
- NotebookEdit modifies cell
- TaskCreate/TaskUpdate/TaskList round-trip
- Task state transitions enforced

---

### Phase 19: Streaming Tool Executor

**Paper Reference:** Section 4.2 — "begins executing tools as they stream in from the
model response, reducing latency for multi-tool responses."

**Rationale:** Currently tools only execute after the full model response is received.
The StreamingToolExecutor reduces latency by starting tool execution as tool_use blocks
arrive in the stream, before the model finishes generating.

**Files to Create/Modify:**

1. CREATE `src/d2c/streaming_executor.py`
2. MODIFY `src/d2c/loop.py` — use StreamingToolExecutor when streaming is enabled

**Key Design:**

```python
class StreamingToolExecutor:
    """
    Paper: "manages concurrent execution with two coordination mechanisms:
    - Sibling abort controller: Fires when any Bash tool errors, immediately
      terminating other in-flight subprocesses.
    - Progress-available signal: Wakes up getRemainingResults() consumer
      when new output is ready."
    """

    def __init__(self, tools_map, permission_engine, hooks):
        self._pending: dict[str, asyncio.Task] = {}
        self._results: dict[str, ToolResult] = {}
        self._abort = asyncio.Event()
        self._progress = asyncio.Event()
        self._order: list[str] = []  # preserve original order

    async def submit(self, tool_use: ToolUse):
        """Submit a tool for execution as soon as it's parsed from the stream."""
        self._order.append(tool_use.id)
        task = asyncio.create_task(self._execute(tool_use))
        self._pending[tool_use.id] = task
        task.add_done_callback(lambda t: self._on_complete(tool_use.id, t))

    async def get_results(self) -> list[tuple[ToolUse, ToolResult]]:
        """Wait for all submitted tools, return in original order."""
        while len(self._results) < len(self._order):
            await self._progress.wait()
            self._progress.clear()
        return [(self._order[i], self._results[tid]) 
                for i, tid in enumerate(self._order)]

    def abort_all(self):
        """Sibling abort: terminate all in-flight tools."""
        self._abort.set()
        for task in self._pending.values():
            task.cancel()
```

**Integration in loop.py streaming path:**
```python
if loop_config.stream:
    executor = StreamingToolExecutor(tools_map, ...)
    async with client.messages.stream(...) as stream:
        async for event in stream:
            if event.type == "content_block_start":
                if event.content_block.type == "tool_use":
                    # Start executing immediately
                    await executor.submit(ToolUse(...))
            elif event.type == "text_delta":
                yield TextDelta(text=event.text)
    
    # Wait for all stream-started tools
    for tu, result in await executor.get_results():
        yield ToolExecutionEvent(tool_use=tu, result=result)
```

**Edge Cases:**
- Tool finishes before stream ends → result buffered
- Stream ends without tools → executor idle, no-op
- Sibling abort mid-execution → remaining tools cancelled
- Mixed text + tool_use in stream → text yielded, tools executed

**Tests (~6):**
- Tool submitted during stream, result after stream ends
- Sibling abort cancels in-flight tools
- Results returned in original submission order
- Single tool stream → executes during streaming
- Zero tools in stream → executor handles gracefully

---

### Phase 20: Deferred Tool Schemas

**Paper Reference:** Section 3.6 — "When ToolSearch is enabled, some tools include
only their names in the initial context; full schemas are loaded on demand."

**Rationale:** Large tool counts (100+ with MCP) consume significant context.
Deferred schemas conserve context by only including tool names initially.

**Files to Create/Modify:**

1. MODIFY `src/d2c/tools/pool.py` — add deferred schema support
2. CREATE `src/d2c/tools/tool_search.py` — ToolSearch tool implementation

**Key Design:**

```python
class DeferredToolSchema:
    """Wrapper for tools whose full schema is loaded on demand."""
    
    def __init__(self, tool: Tool):
        self.tool = tool
        self._schema_loaded = False
    
    def to_api_format(self) -> dict:
        if self._schema_loaded:
            return self.tool.to_api_format()
        # Return minimal: name only (model knows how to use ToolSearch)
        return {
            "name": self.tool.name,
            "description": f"Use ToolSearch with query=\"{self.tool.name}\" for full schema.",
        }
    
    def load_full_schema(self):
        self._schema_loaded = True

class ToolSearchTool(Tool):
    """Enables the model to search for and load tool schemas on demand."""
    name = "ToolSearch"
    description = "Search for tools and load their full schemas."
    category = PermissionCategory.META
    is_concurrent_safe = True

    async def execute(self, query: str = None) -> ToolResult:
        if query:
            # Fuzzy match tool names and descriptions
            matching = [t for t in all_tools if query.lower() in t.name.lower()]
            # Load full schemas for matches
            for tool in matching:
                if isinstance(tool, DeferredToolSchema):
                    tool.load_full_schema()
            return ToolResult(output=format_matches(matching))
        else:
            # Return all tool names
            return ToolResult(output=format_all_tools(all_tools))
```

**Config:** `deferred_tools: bool = False` in CompactConfig. When enabled, tools
with large schemas (>500 chars) or MCP tools use deferred loading.

**Tests (~5):**
- Deferred tool shows only name in API format
- ToolSearch loads full schema
- ToolSearch fuzzy matches tool names
- ToolSearch with no query returns all tools
- Normal tools unaffected by deferred mode

---

### Phase 21: Path-Scoped Rules

**Paper Reference:** Section 7.2 — ".d2c/rules/*.md loaded lazily when new
directories are read, potentially changing classifier behavior mid-conversation."

**Rationale:** Path-scoped rules allow different permission and behavior rules for
different parts of the codebase (e.g., stricter rules for auth/, relaxed for docs/).

**Files to Create/Modify:**

1. MODIFY `src/d2c/memory.py` — add lazy path-scoped rule loading
2. MODIFY `src/d2c/permissions.py` — dynamic rule updates when path rules load

**Key Design:**

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
            # Format: deny/allow pattern reason
            rule = self._parse_rule_file(frontmatter, body)
            new_rules.append(rule)
        
        self._loaded_dirs.add(parent)
        self._rules[parent] = new_rules
        return new_rules
    
    def _parse_rule_file(self, frontmatter, body) -> Rule:
        """Parse a .d2c/rules/*.md file into a Rule."""
        # Frontmatter: type (deny/allow), pattern (glob)
        # Body: reason description (for the model, not enforced)
        ...
```

**Integration:** Wire into `LazyMemoryLoader.on_file_accessed()` to also load
path-scoped rules. Add loaded rules to PermissionEngine dynamically.

**Edge Cases:**
- Rules in parent directories apply to all children
- Child directory rules override parent rules for that subtree
- Rules loaded mid-conversation apply immediately to subsequent tool calls
- Circular or conflicting rules → first-match wins

**Tests (~6):**
- Path rules loaded when file accessed in directory
- Path rules not loaded twice for same directory
- Child rules don't affect sibling directories
- Path rules integrated into permission engine
- Rules in parent directory apply to children
- Empty rules directory → no error

---

### Phase 22: Global Prompt History

**Paper Reference:** Section 9.1 — "Global prompt history: User prompts only,
stored in history.jsonl at the Claude configuration home directory."

**Rationale:** Enables Up-arrow / ctrl+r navigation in interactive mode, matching
the paper's "history.ts → makeHistoryReader() yields entries in reverse order."

**Files to Create/Modify:**

1. CREATE `src/d2c/history.py` — global prompt history management
2. MODIFY `src/d2c/main.py` — integrate history into interactive REPL

**Key Design:**

```python
class PromptHistory:
    """Global prompt history, stored as JSONL in ~/.d2c/history.jsonl."""
    
    def __init__(self, base_dir: Path):
        self.history_path = base_dir / "history.jsonl"
    
    def append(self, prompt: str, metadata: dict | None = None) -> None:
        """Record a user prompt."""
        entry = {
            "prompt": prompt,
            "timestamp": _utc_now(),
            "cwd": str(Path.cwd()),
            "metadata": metadata or {},
        }
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    
    def read_reverse(self, limit: int = 1000) -> list[dict]:
        """Read most recent entries in reverse order (for ctrl+r)."""
        if not self.history_path.exists():
            return []
        # readLinesReverse equivalent
        with open(self.history_path, "rb") as f:
            lines = _read_lines_reverse(f, limit)
        return [json.loads(line) for line in reversed(lines)]
    
    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Fuzzy search history for ctrl+r navigation."""
        entries = self.read_reverse()
        matches = [e for e in entries if query.lower() in e["prompt"].lower()]
        return matches[:limit]
```

**Interactive REPL integration:**
```python
# In run_interactive():
history = PromptHistory(Path.home() / ".d2c")

while True:
    try:
        prompt_text = input("> ").strip()
        if prompt_text:
            history.append(prompt_text)
    except (EOFError, KeyboardInterrupt):
        break
```

**Tests (~5):**
- History append and readback
- read_reverse returns most recent first
- Search finds matching prompts
- Empty history → no errors
- Large history limited in search results

---

### Phase 23: File History Checkpoints (--rewind-files)

**Paper Reference:** Section 9.2 — "File-history checkpoints for --rewind-files,
stored at ~/.claude/file-history/<sessionId>/."

**Rationale:** The paper specifically mentions file-history checkpoints as a
recovery mechanism. They enable reverting filesystem changes made during a session.

**Files to Create/Modify:**

1. CREATE `src/d2c/file_history.py` — file snapshot and restore
2. MODIFY `src/d2c/main.py` — add `--rewind-files` flag

**Key Design:**

```python
class FileHistory:
    """File-level snapshots for reverting filesystem changes."""
    
    def __init__(self, base_dir: Path, session_id: str):
        self.checkpoint_dir = base_dir / "file-history" / session_id
    
    def checkpoint(self, file_path: Path) -> None:
        """Save a copy of a file before modification."""
        rel_path = file_path.resolve().relative_to(Path.cwd())
        target = self.checkpoint_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if file_path.exists():
            shutil.copy2(file_path, target)
    
    def rewind(self, file_path: Path) -> bool:
        """Restore a file to its checkpointed state."""
        rel_path = file_path.resolve().relative_to(Path.cwd())
        source = self.checkpoint_dir / rel_path
        if source.exists():
            shutil.copy2(source, file_path)
            return True
        return False
    
    def rewind_all(self) -> list[Path]:
        """Restore all checkpointed files. Returns list of restored paths."""
        restored = []
        for root, dirs, files in os.walk(self.checkpoint_dir):
            for f in files:
                rel = Path(root) / f
                target = Path.cwd() / rel.relative_to(self.checkpoint_dir)
                shutil.copy2(rel, target)
                restored.append(target)
        return restored

class FileHistoryTracker:
    """Wraps WriteTool and EditTool to automatically checkpoint before writes."""
    
    def __init__(self, history: FileHistory):
        self.history = history
    
    def before_write(self, file_path: Path) -> None:
        """Called before Write/Edit modifies a file."""
        self.history.checkpoint(file_path)
```

**Integration in WriteTool/EditTool:**
```python
# Before writing, checkpoint the original
file_history.before_write(file_path)
```

**CLI:**
```bash
d2c --rewind-files  # reverts all file changes from this session
```

**Edge Cases:**
- File not checkpointed (never written) → rewind is no-op
- Binary files → checkpoints work as binary copies
- Checkpoint directory grows large → per-session, cleaned on session end
- File deleted after checkpoint → rewind restores it

**Tests (~6):**
- Checkpoint saves file copy
- Rewind restores file from checkpoint
- Rewind all restores multiple files
- File not checkpointed → rewind no-op
- Checkpoint with nested directories
- Empty checkpoint dir → rewind all no-op

---

### Phase 24: Background Subagents

**Paper Reference:** Section 8 — "run_in_background field is omitted when
background tasks are disabled"

**Rationale:** Background subagents enable fire-and-forget task delegation.
The paper describes them in the Agent tool schema.

**Files to Create/Modify:**

1. MODIFY `src/d2c/subagent.py` — add background execution
2. MODIFY `src/d2c/tools/agent_tool.py` — wire background flag

**Key Design:**

```python
class BackgroundSubagentManager:
    """Manages background subagent execution and notification."""
    
    def __init__(self):
        self._running: dict[str, asyncio.Task] = {}
    
    async def launch_background(self, definition, task_prompt, ...):
        """Launch a subagent in the background. Returns immediately with a handle."""
        subagent_id = str(uuid4())[:8]
        task = asyncio.create_task(
            spawn_subagent(definition, task_prompt, ...)
        )
        self._running[subagent_id] = task
        task.add_done_callback(
            lambda t: self._on_complete(subagent_id, t)
        )
        return subagent_id
    
    def get_status(self, subagent_id: str) -> str:
        """Check status of a background subagent."""
        if subagent_id not in self._running:
            return "unknown"
        task = self._running[subagent_id]
        if task.done():
            return "completed" if not task.exception() else "failed"
        return "running"
    
    def get_result(self, subagent_id: str) -> SubagentResult | None:
        task = self._running.get(subagent_id)
        if task and task.done() and not task.exception():
            return task.result()
        return None

# AgentTool.execute():
async def execute(self, prompt, ..., run_in_background=False):
    if run_in_background:
        bg_id = await bg_manager.launch_background(definition, prompt, ...)
        return ToolResult(
            output=f"Subagent launched in background. ID: {bg_id}\nUse 'check background {bg_id}' for status.",
            metadata={"background": True, "subagent_id": bg_id},
        )
    else:
        result = await spawn_subagent(definition, prompt, ...)
        return ToolResult(output=result.summary, ...)
```

**Edge Cases:**
- Background subagent fails silently → error logged, result shows failure
- Multiple background agents running → each tracked independently
- Parent session ends with running background → cancel or warn
- Background agent results → model can query status and retrieve

**Tests (~5):**
- Background subagent returns immediately
- Status check on running agent
- Status check on completed agent
- Background agent failure captured
- Multiple concurrent background agents

---

### Phase 25: KAIROS Persistent Background Agent (Feature-Gated)

**Paper Reference:** Section 11.6 — "Feature-gated KAIROS system: persistent
background agent with tick-based heartbeats."

**Rationale:** The paper's most forward-looking feature. A background agent that
periodically wakes, checks for tasks, and acts autonomously. This is speculative
and should be feature-gated.

**Files to Create/Modify:**

1. CREATE `src/d2c/kairos.py` — heartbeat-based background agent
2. MODIFY `src/d2c/config.py` — add KAIROS feature flag

**Key Design:**

```python
class KairosAgent:
    """
    Paper: "A persistent background agent with tick-based heartbeats:
    when no user messages are pending, the system injects periodic <tick>
    prompts, and the model decides whether to act or sleep."
    
    Key design choices:
    - Terminal focus awareness: maximizes autonomous action when user is away,
      increases collaboration when present.
    - Economic throttling via SleepTool: each wake-up costs an API call;
      prompt cache expires after 5 minutes of inactivity.
    - Feature-gated: only active when KAIROS_ENABLED=true.
    """
    
    def __init__(self, config, loop_config, idle_timeout=30):
        self._idle_timeout = idle_timeout  # seconds before ticking
        self._last_user_activity = time.monotonic()
        self._tick_count = 0
        self._sleeping = False
        self._prompt_cache_age = 0
    
    async def start(self) -> AsyncGenerator:
        """Start the KAIROS heartbeat loop. Yields actions when agent acts."""
        while True:
            await asyncio.sleep(self._idle_timeout)
            
            if time.monotonic() - self._last_user_activity < self._idle_timeout:
                continue  # User is active, don't interrupt
            
            if self._sleeping:
                continue  # Agent chose to sleep
            
            # Inject tick prompt
            tick_prompt = f"<tick> Tick #{self._tick_count}. No user messages pending. You may act or sleep.</tick>"
            self._tick_count += 1
            
            # Call model with tick (lightweight, no tools by default)
            response = await self._tick_call(tick_prompt)
            
            if response.action == "sleep":
                self._sleeping = True
                yield SleepEvent(duration=response.sleep_duration)
            elif response.action == "act":
                yield ActionEvent(task=response.task)
                # Execute the action through a subagent
    
    def on_user_activity(self):
        """Called when user sends a message. Resets idle timer."""
        self._last_user_activity = time.monotonic()
        self._sleeping = False
```

**Feature gate:**
```python
# Config
kairos_enabled: bool = False  # Feature-gated, off by default

# In main.py interactive:
if config.kairos_enabled:
    kairos = KairosAgent(config, loop_config)
    async for event in kairos.start():
        # Merge KAIROS actions into the main loop
        ...
```

**Edge Cases:**
- KAIROS disabled → zero overhead, no tick loop
- User returns during sleep → wake immediately
- Prompt cache expires → sleep is cheaper than waking
- KAIROS action conflicts with user action → user wins

**Tests (~4):**
- KAIROS idle timeout triggers tick
- User activity resets idle timer
- Sleep state prevents ticks
- Feature flag off → no KAIROS behavior

---

## Summary

| Phase | Priority | Feature | Est. Tests |
|---|---|---|---|
| 11 | HIGH | MCP Integration | 15 |
| 12 | HIGH | 3 Missing Compaction Shapers | 15 |
| 13 | HIGH | Plugin System | 10 |
| 14 | MEDIUM | Auto + Bypass Permission Modes | 12 |
| 15 | MEDIUM | 18 Remaining Hook Events | 10 |
| 16 | MEDIUM | Worktree Isolation | 8 |
| 17 | MEDIUM | Shell Sandboxing | 10 |
| 18 | LOW | Missing Core Tools | 15 |
| 19 | LOW | Streaming Tool Executor | 6 |
| 20 | LOW | Deferred Tool Schemas | 5 |
| 21 | LOW | Path-Scoped Rules | 6 |
| 22 | LOW | Global Prompt History | 5 |
| 23 | LOW | File History Checkpoints | 6 |
| 24 | LOW | Background Subagents | 5 |
| 25 | LOW | KAIROS Persistent Background Agent | 4 |

**Total estimated new tests: ~132**
**Projected final test count: 378 + 132 = ~510 tests**
