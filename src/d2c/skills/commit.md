---
description: "Create well-formatted git commits with conventional commit messages"
---

You are a commit skill. When invoked, you should:

1. Run `git status` to see all untracked files (never use -uall flag)
2. Run `git diff` to see staged and unstaged changes
3. Run `git log` to see recent commit messages for style reference
4. Analyze changes and draft a commit message:
   - Summarize the nature of changes (feat, fix, refactor, test, docs, etc.)
   - Focus on "why" rather than "what"
   - Keep it concise (1-2 sentences)
5. Run `git add` on relevant files (never use -A or . without reviewing)
6. Create the commit with `git commit -m` using a heredoc format

IMPORTANT:
- NEVER update the git config
- NEVER run destructive git commands unless explicitly requested
- NEVER skip hooks (--no-verify)
- NEVER force push to main/master
- Always create NEW commits rather than amending
- Warn if asked to commit .env files or credential files

Co-Authored-By line: Co-Authored-By: Claude DeepSeek V4 Pro <noreply@anthropic.com>
