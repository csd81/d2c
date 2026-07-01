# Security model

d2c is a local-first coding agent. Its safety model is **policy-level** (permission gating, trust
gating, redaction, read-before-write) rather than OS-enforced isolation. This document states what
is protected, what is not, and what you should not rely on. The invariants below are enforced by
`tests/test_security_regressions.py` so they fail in CI if they regress.

## Known protections

- **Permission gate (deny-first).** Every tool call is evaluated before execution. Deny rules always
  win. Under `acceptEdits`, destructive shell (`rm`, `mv`, `sed -i`, `find -delete`, pipe-to-shell,
  interpreter `-c`/`-lc`, `sudo`, `chmod -R`, wrapper-hidden variants like `env bash -c`) is
  **denied**; uncertain commands **ask**. Nothing destructive is silently allowed.
- **`ASK` never auto-executes.** In the REPL you're prompted (`[y/N/a]`, default deny; `y`/`yes`
  approves once; `a` "always" allows that **exact** action for the session); headless / MCP /
  no-callback contexts return a permission-required denial (MCP has no terminal, so it never blocks
  on stdin). Permission-evaluation errors **fail closed**. Every decision is audited
  (`permission_ask`/`approved`/`approved_cached`/`denied`/`required`/`approval_error`, correlated by
  tool-call id, no secrets).
- **Session-scoped approvals only.** The `a` cache is **in-memory**, stores only SHA-256 hashes of
  the exact action (never raw input), matches exact repeats only (no shell generalization), and is
  **never persisted** — cleared on `/clear`/`/resume`/`/fork` and on restart.
- **Read-before-Write.** Write/Edit/ReplaceMany/JsonEdit require the file to have been Read first.
  Paths are **canonicalized** (`..`/`.`/symlinks resolved), so alternate spellings or a symlink alias
  cannot bypass the guard, and you can't read one realpath then mutate a different one.
- **Absolute paths only.** File tools reject relative paths (including `../` traversal) outright.
- **Trust gate.** Untrusted workspaces skip project-local `.env`, plugins, skills, MCP config, and
  CLAUDE.md/memory — the executable-ish extension surfaces — and force `default`/`plan` mode.
- **Secret redaction.** Audit logs redact API keys (`sk-…`/`tvly-…`), `Authorization`,
  `X-Api-Key`/`X-Subscription-Token`, literal env secrets, and password/token fields. WebSearch and
  permission errors never include the key. Full prompts/tool-outputs are not logged by default.
- **Subagent isolation.** Subagents run with their own context and tool pool; optional git-worktree
  isolation for filesystem separation.

## Known limitations (do not over-rely)

- **The sandbox is not a filesystem jail.** The default `process` backend restricts environment, cwd,
  and timeout — it does **not** prevent a command from writing outside cwd. Filesystem safety for
  destructive commands comes from the **permission gate**, not the sandbox. The Docker backend
  (`backend=docker`, opt-in) provides real isolation; the Windows backend is a stub that falls back
  to the process backend.
- **No cwd jail at the tool level.** File tools operate on any absolute path the permission policy
  allows; there is no path-jail confining them to the project directory.
- **Prompt injection is mitigated by design, not filtered.** Untrusted text from memory / WebFetch /
  WebSearch is carried as data/context; no code path executes retrieved text or changes permission
  state from it. But d2c does not scrub injected instructions from model context — treat retrieved
  content as untrusted.
- **`ASK` outside the REPL blocks rather than prompts.** There is no interactive approval channel in
  headless/MCP mode; such actions are denied (permission-required), not queued.
- **Policy, not proof.** These are runtime checks, not formally verified guarantees.

## What not to rely on

- Do not rely on the process sandbox for isolating malicious code — use `default`/`plan` mode,
  deny rules, the Docker sandbox, or a container/VM.
- Do not run d2c against an untrusted repository with `--trust` unless you accept executing its
  local extensions.
- Do not put secrets in tool inputs/prompts expecting redaction to catch every shape — redaction
  covers known secret shapes, not arbitrary sensitive strings.

## Reporting

Open a GitHub issue (or a private security advisory) with a minimal reproduction. Please don't
include real secrets in the report.
