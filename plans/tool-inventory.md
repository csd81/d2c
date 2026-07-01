# Tool inventory & gap table

Snapshot of built-in tools in `d2c` vs. the paper's ~54-tool surface, and what is
intentionally deferred. Updated in Phase 41.

## Current built-ins (23)

| Category | d2c tools | Permission |
|---|---|---|
| File read | Read, Glob, Grep, ListDir, FileInfo | READ |
| File write | Write, Edit, NotebookEdit, ReplaceMany, JsonEdit | WRITE |
| Shell | Bash | SHELL |
| Git | GitStatus, GitDiff | READ |
| Web | WebFetch, WebSearch | READ |
| Tasks | TaskCreate, TaskUpdate, TaskList | META / READ |
| Memory | Remember | META |
| Agents | Agent, AgentStatus | META / READ |
| Skills | Skill | META |
| Tool search | ToolSearch | META |
| MCP | (dynamic per connected server) | varies |

## Gap table vs. the paper

| Category | Paper tool(s) | d2c equivalent | Missing? | Priority | Notes |
|---|---|---|---|---|---|
| file read/write/edit | Read/Write/Edit/MultiEdit | Read/Write/Edit/**ReplaceMany**/**JsonEdit** | covered | — | ReplaceMany ≈ MultiEdit |
| search/glob | Glob/Grep | Glob/Grep | covered | — | ripgrep-backed |
| shell/process | Bash/PowerShell/KillShell | Bash (+ sandbox) | partial | LOW | background via Bash; no separate KillShell |
| web | WebFetch/WebSearch | WebFetch/WebSearch (Tavily) | covered | — | Phase 39 |
| notebooks | NotebookEdit | NotebookEdit | covered | — | |
| tasks/todos | TodoWrite | TaskCreate/Update/List | covered | — | |
| agents/subagents | Task/Agent | Agent/AgentStatus | covered | — | Phase 34 |
| memory/skills | Memory/Skill | Remember/Skill | covered | — | |
| diagnostics/status | (various) | FileInfo/ListDir/GitStatus | partial | — | Phase 41 |
| git helpers | (via Bash in CC) | **GitStatus/GitDiff** | added | — | Phase 41 |
| planning/spec | ExitPlanMode | — | deferred | LOW | plan mode is a harness feature, not a tool |
| image/browser/UI | screenshot / browser | — | deferred | LOW | better via MCP; needs a browser runtime |

## Intentionally deferred

- **Browser / screenshot / computer-use tools** — require a browser or GUI runtime; better delivered
  through an MCP server than a built-in.
- **Provider-specific tools** — anything needing extra secret configuration beyond WebSearch.
- **KillShell / process-management** — background execution already exists via `Bash(run_in_background)`.
- **A full MultiEdit/patch-apply** beyond `ReplaceMany` — diffs are better handled by Edit/ReplaceMany
  for now.
- **Reaching ~54 tools** — the remaining breadth is mostly product-specific or platform-specific and
  is out of scope for an educational port.
