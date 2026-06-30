# Phase 16: Worktree Isolation for Subagents

**Paper Reference:** Section 8.2 — "Creates a temporary git worktree, giving the
subagent its own copy of the repository to modify without affecting the parent's
working tree."

**Priority:** MEDIUM

## Rationale

Worktree isolation is the primary filesystem isolation mechanism for subagents. It enables
"safe" exploration without contaminating the parent's workspace. The paper describes it as
a zero-dependency isolation approach leveraging Git's built-in mechanism.

## Files to Create/Modify

1. CREATE `src/d2c/worktree.py` — git worktree management
2. MODIFY `src/d2c/subagent.py` — add worktree isolation mode

## Key Design

```python
@dataclass
class WorktreeContext:
    worktree_path: Path
    branch_name: str
    original_repo: Path

class WorktreeManager:
    """Manages git worktree lifecycle for subagent isolation."""

    def create(self, repo_path: Path, branch_name: str | None = None) -> WorktreeContext:
        if branch_name is None:
            branch_name = f"d2c-subagent-{uuid4().hex[:8]}"
        worktree_path = repo_path.parent / f".d2c-worktrees/{branch_name}"
        subprocess.run(["git", "worktree", "add", str(worktree_path), "-b", branch_name])
        return WorktreeContext(worktree_path, branch_name, repo_path)

    def remove(self, ctx: WorktreeContext) -> None:
        subprocess.run(["git", "worktree", "remove", str(ctx.worktree_path), "--force"])
        subprocess.run(["git", "branch", "-D", ctx.branch_name])

    def get_changes(self, ctx: WorktreeContext) -> str:
        result = subprocess.run(
            ["git", "-C", str(ctx.worktree_path), "diff"],
            capture_output=True, text=True
        )
        return result.stdout
```

## Subagent Integration

```python
async def spawn_subagent(definition, task_prompt, ..., isolation_mode="default"):
    worktree_ctx = None
    try:
        if isolation_mode == "worktree":
            if not is_git_repo(parent_config.cwd):
                raise ValueError("Worktree isolation requires a git repository")
            worktree_ctx = worktree_manager.create(parent_config.cwd)
            subagent_cwd = worktree_ctx.worktree_path
        # ... run subagent loop in isolated cwd ...
    finally:
        if worktree_ctx:
            worktree_manager.remove(worktree_ctx)
```

## Edge Cases

- Not a git repo → error, fall back to in-process isolation
- Worktree creation fails (disk full, permissions) → error
- Subagent modifies files in worktree → diff captured in result
- Cleanup fails → log warning, leave worktree for manual cleanup

## Tests (~8)

- Worktree creation succeeds in git repo
- Subagent runs in worktree isolation
- Worktree changes don't affect parent repo
- Diff captured correctly
- Cleanup removes worktree
- Non-git repo → error with clear message
- Worktree creation failure → error
- Cleanup failure → warning, no crash
