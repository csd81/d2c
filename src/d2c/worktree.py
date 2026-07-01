"""Git worktree management for subagent isolation. Paper Section 8.2.

"Creates a temporary git worktree, giving the subagent its own copy of the
repository to modify without affecting the parent's working tree."

Zero-dependency isolation approach leveraging Git's built-in mechanism.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class WorktreeContext:
    """A created worktree and its metadata."""

    worktree_path: Path
    branch_name: str
    original_repo: Path


class WorktreeError(Exception):
    """Base error for worktree operations."""


class NotAGitRepoError(WorktreeError):
    """The given path is not inside a git repository."""


class WorktreeCreationError(WorktreeError):
    """Worktree creation failed."""


class WorktreeCleanupError(WorktreeError):
    """Worktree removal failed (non-fatal — logged as warning)."""


class WorktreeManager:
    """Manages git worktree lifecycle for subagent isolation.

    Usage:
        manager = WorktreeManager()
        try:
            ctx = manager.create(repo_path)
            # ... run subagent in ctx.worktree_path ...
            diff = manager.get_changes(ctx)
        finally:
            manager.remove(ctx)
    """

    def __init__(self, worktrees_base: Path | None = None) -> None:
        if worktrees_base is None:
            worktrees_base = Path.home() / ".d2c" / "worktrees"
        self._worktrees_base = worktrees_base

    def is_git_repo(self, path: Path) -> bool:
        """Check if path is inside a git repository."""
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--git-dir"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.TimeoutError, FileNotFoundError, OSError):
            return False

    def _find_repo_root(self, path: Path) -> Path:
        """Find the root of the git repository containing path."""
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except (subprocess.TimeoutError, FileNotFoundError, OSError):
            pass
        raise NotAGitRepoError(f"Not a git repository: {path}")

    def create(
        self,
        repo_path: Path,
        branch_name: str | None = None,
    ) -> WorktreeContext:
        """Create a git worktree for subagent isolation.

        Creates a new branch and checks it out in an isolated worktree directory.
        The worktree is placed under ~/.d2c/worktrees/ by default.

        Args:
            repo_path: Path inside the git repository.
            branch_name: Branch name for the worktree. Auto-generated if None.

        Returns:
            WorktreeContext with worktree path, branch name, and repo root.

        Raises:
            NotAGitRepoError: If repo_path is not inside a git repository.
            WorktreeCreationError: If worktree creation fails.
        """
        if not self.is_git_repo(repo_path):
            raise NotAGitRepoError(f"Cannot create worktree: '{repo_path}' is not a git repository")

        repo_root = self._find_repo_root(repo_path)

        if branch_name is None:
            branch_name = f"d2c-subagent-{uuid4().hex[:8]}"

        worktree_path = self._worktrees_base / branch_name

        try:
            self._worktrees_base.mkdir(parents=True, exist_ok=True)

            # git worktree add <path> -b <branch>
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "worktree",
                    "add",
                    str(worktree_path),
                    "-b",
                    branch_name,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            raise WorktreeCreationError(
                f"Worktree creation failed: {e.stderr.strip() if e.stderr else e}"
            ) from e
        except subprocess.TimeoutError as e:
            raise WorktreeCreationError("Worktree creation timed out after 30s") from e
        except OSError as e:
            raise WorktreeCreationError(f"Failed to create worktree directory: {e}") from e

        logger.info(
            "Created worktree '%s' at %s (branch: %s)",
            branch_name,
            worktree_path,
            branch_name,
        )

        return WorktreeContext(
            worktree_path=worktree_path,
            branch_name=branch_name,
            original_repo=repo_root,
        )

    def remove(self, ctx: WorktreeContext) -> None:
        """Remove a worktree and its associated branch.

        Cleanup failures are logged as warnings but do not crash.
        The worktree is left on disk for manual cleanup if removal fails.

        Args:
            ctx: The WorktreeContext returned by create().
        """
        # Remove worktree
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(ctx.original_repo),
                    "worktree",
                    "remove",
                    str(ctx.worktree_path),
                    "--force",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(
                "Failed to remove worktree '%s': %s",
                ctx.worktree_path,
                e.stderr.strip() if e.stderr else e,
            )
        except (subprocess.TimeoutError, OSError) as e:
            logger.warning(
                "Failed to remove worktree '%s': %s",
                ctx.worktree_path,
                e,
            )

        # Delete branch
        try:
            subprocess.run(
                ["git", "-C", str(ctx.original_repo), "branch", "-D", ctx.branch_name],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(
                "Failed to delete branch '%s': %s",
                ctx.branch_name,
                e.stderr.strip() if e.stderr else e,
            )
        except (subprocess.TimeoutError, OSError) as e:
            logger.warning(
                "Failed to delete branch '%s': %s",
                ctx.branch_name,
                e,
            )

        logger.info(
            "Removed worktree '%s' (branch: %s)",
            ctx.worktree_path,
            ctx.branch_name,
        )

    def get_changes(self, ctx: WorktreeContext) -> str:
        """Get the diff of changes made in the worktree.

        Returns '' if no changes were made or if git diff fails.

        Args:
            ctx: The WorktreeContext returned by create().

        Returns:
            The git diff output, or '' on error.
        """
        try:
            result = subprocess.run(
                ["git", "-C", str(ctx.worktree_path), "diff", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout if result.returncode == 0 else ""
        except (subprocess.TimeoutError, OSError):
            return ""

    def get_changed_files(self, ctx: WorktreeContext) -> list[str]:
        """Get list of files changed in the worktree.

        Args:
            ctx: The WorktreeContext returned by create().

        Returns:
            List of changed file paths relative to worktree root.
        """
        try:
            result = subprocess.run(
                ["git", "-C", str(ctx.worktree_path), "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return [f for f in result.stdout.strip().split("\n") if f]
        except (subprocess.TimeoutError, OSError):
            pass
        return []
