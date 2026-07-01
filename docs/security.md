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
- **Untrusted-content delimiting.** WebFetch pages, WebSearch snippets, and recalled auto-memories
  are wrapped in explicit `<untrusted_web_content>`/`<untrusted_memory_content>` tags (embedded
  closing tags are neutralized so content can't break out of the wrapper), and the system prompt
  instructs the model to treat tagged content as data. CLAUDE.md sections carry `<!-- LEVEL: path -->`
  provenance markers.
- **WebSearch provider trust (Phase 58).** `D2C_WEBSEARCH_PROVIDER` picks who receives your query
  text: `tavily`/`brave` are third-party hosted APIs (your key, their infra); `searxng` sends the
  query to whatever `D2C_WEBSEARCH_BASE_URL` you configure — only point it at an instance you trust,
  since d2c has no way to verify a self-hosted endpoint's provenance or behavior. All three return
  results through the same untrusted-content wrapper above regardless of provider.
- **Subagent isolation.** Subagents run with their own context and tool pool; optional git-worktree
  isolation for filesystem separation.
- **Subagent profiles are trust-gated (Phase 61).** Project-local subagent definitions
  (`.d2c/agents/*.yaml` capability profiles and legacy `.d2c/agents/*.md` agents) carry
  executable-ish config — a custom system prompt, a permission mode, and tool allow/deny boundaries —
  so they load **only in a trusted workspace**, the same gate as `.env`/skills/MCP/CLAUDE.md. Built-in
  types (`Explore`/`Plan`/`General-purpose`) always resolve; only project-local definitions are
  gated. Loading fails closed (an undecided/broken trust gate loads nothing project-local), and a
  malformed profile is reported and skipped, never applied. A profile cannot escalate beyond what the
  session already permits — its `permission_mode` selects among the same modes, and its tool
  allow/deny only ever *narrows* the pool.
- **Scoped settings, managed lock (Phase 60).** `Config.load()` layers `permission_mode` /
  `sandbox_enabled` / `permission_rules` / `hooks` from managed (`/etc/d2c/settings.yaml`) → user
  (`~/.d2c/settings.yaml`) → project → local YAML. A value set at the managed scope **cannot be
  overridden** by any lower scope — attempted overrides are recorded and surfaced as warnings, never
  silently applied. `permission_rules`/`hooks` are **unioned** across scopes rather than replaced, so
  a managed deny rule always survives into the engine regardless of what a project/local scope adds
  (the permission engine checks all deny rules before any allow rule). Project/local settings are
  trust-gated the same as `.env`/CLAUDE.md. Malformed entries are reported, not applied, and never
  crash the session.

## Known limitations (do not over-rely)

- **The `process` sandbox backend is not a filesystem jail.** The default `process` backend restricts
  environment, cwd, and timeout — it does **not** prevent a command from writing outside cwd.
  Filesystem safety for destructive commands under this backend comes from the **permission gate**,
  not the sandbox. The Windows backend is a stub that falls back to the process backend.
- **OS-level backends provide real isolation (Phase 62).** `D2C_SANDBOX_BACKEND=bubblewrap` (Linux)
  runs each sandboxed command inside a bubblewrap namespace: the working directory (plus any
  `allowed_dirs`) is bind-mounted **read-write**, system roots (`/usr`, `/bin`, `/lib`, `/etc`, ...)
  **read-only**, `/tmp` a fresh tmpfs, the network namespace unshared (no network unless
  `D2C_SANDBOX_NETWORK=1`), and the process dies with its parent — so a write to `$HOME` or `/etc`, or
  a read of an unbound sibling directory, actually fails. `backend=docker` (opt-in) is the other
  strong option. If a requested OS-level backend is unavailable, d2c **fails closed** (the command
  does not run) unless `D2C_SANDBOX_FALLBACK=1` opts into the weaker process backend — a requested
  strong sandbox never silently downgrades. `--doctor` reports the configured backend and its
  availability. These backends still only apply to commands the sandbox decides to run inside it (safe
  read-only commands skip it for performance); they are defense-in-depth, layered under the permission
  gate, not a replacement for it.
- **No cwd jail at the tool level.** File tools operate on any absolute path the permission policy
  allows; there is no path-jail confining them to the project directory.
- **Prompt injection is mitigated by design, not filtered.** Untrusted text from memory / WebFetch /
  WebSearch is carried as data/context; no code path executes retrieved text or changes permission
  state from it. Retrieved content is delimited (see above) and the model is instructed to treat it
  as data — but d2c does not scrub injected instructions from model context, and delimiters guide
  the model rather than constrain it. The permission gate remains the enforcement layer.
- **`ASK` outside the REPL blocks rather than prompts.** There is no interactive approval channel in
  headless/MCP/`--serve` mode; such actions are denied (permission-required), not queued.
- **The local HTTP server (`--serve`, Phase 59) has no authentication.** It binds `127.0.0.1` by
  default; anyone who can reach the bound host/port can create sessions and run prompts with
  whatever permission mode the server was started with. It's groundwork for a local daemon, not a
  production server — no TLS, no auth, no rate limiting. Don't bind it beyond localhost. Tool inputs
  in `/sessions/{id}/events` responses are redacted the same way audit logs are, but redaction covers
  known secret shapes, not everything sensitive.
- **Policy, not proof.** These are runtime checks, not formally verified guarantees.

## What not to rely on

- Do not rely on the process sandbox for isolating malicious code — use `default`/`plan` mode,
  deny rules, the Docker sandbox, or a container/VM.
- Do not run d2c against an untrusted repository with `--trust` unless you accept executing its
  local extensions.
- Do not put secrets in tool inputs/prompts expecting redaction to catch every shape — redaction
  covers known secret shapes, not arbitrary sensitive strings.
- Do not put secrets in scoped settings YAML (`settings.yaml`/`settings.local.yaml`) — those files
  are meant for policy (permission mode, rules, hooks), not credentials; keep secrets in `.env` or
  environment variables, which have their own (still not foolproof) redaction/trust handling.

## Reporting

Open a GitHub issue (or a private security advisory) with a minimal reproduction. Please don't
include real secrets in the report.
