# Phase 73: Markdown rendering in the REPL

**Priority:** HIGH (improves every interactive assistant response)

## Context

The interactive REPL currently prints assistant text as plain output. This makes
normal model responses harder to scan, especially when they contain headings,
lists, fenced code blocks, inline code, links, or command snippets.

The project already uses `prompt_toolkit` for the REPL, status bar, and
permission dialogs. Phase 73 should improve rendering in the existing terminal
experience without changing headless output or adding a heavy UI dependency.

## Goal

Render common Markdown constructs in interactive mode so assistant responses are
easier to read while preserving plain text behavior everywhere else.

Primary goals:

1. Render headings, lists, code fences, inline code, and links legibly.
2. Keep headless/single-shot output unchanged.
3. Fail open to plain text if rendering encounters malformed Markdown.
4. Avoid large dependencies and full-screen UI changes.

## Scope

In scope:

- Markdown renderer for REPL assistant text
- integration in interactive output path
- styles that work in normal terminals
- tests for renderer behavior and fallback
- docs/changelog update

Out of scope:

- full CommonMark compliance
- tables beyond a readable plain-text fallback
- HTML rendering
- syntax highlighting with language grammars
- clickable terminal links
- rendering tool outputs as Markdown unless already safe and intentional
- changing SDK/headless/MCP output

## Rendering Targets

Support a pragmatic subset:

| Markdown | REPL rendering |
|---|---|
| `# Heading` | bold/accent heading line |
| `## Heading` | bold heading line |
| `- item` / `* item` | indented bullet line |
| `1. item` | indented numbered line |
| fenced code blocks | visually separated monospace block |
| inline code | accent/monospace span |
| `[text](url)` | `text (url)` or styled `text` plus visible URL |
| blockquotes | muted/indented line |

Tables can remain plain text in v1.

## Design Direction

Prefer a small local renderer over a heavy Markdown dependency unless the
existing dependency set already includes a suitable parser.

Potential shape:

```python
def render_markdown_for_repl(text: str) -> FormattedText:
    ...
```

or:

```python
def markdown_to_formatted_text(text: str) -> list[tuple[str, str]]:
    ...
```

Use `prompt_toolkit.formatted_text` primitives that the project already uses for
permission dialogs/status bars.

Safety rules:

- Renderer must never execute or interpret HTML.
- Renderer must never fetch links.
- Renderer must not read files referenced in Markdown.
- On any parsing/rendering error, print the original text.

## Integration

Find the interactive path that prints `TextResponse` or final assistant text in
`src/d2c/main.py`.

Behavior:

- interactive REPL: render Markdown
- single-shot prompt: unchanged plain text
- `--json`, SDK, MCP, eval: unchanged structured/plain output

If streaming deltas are printed token-by-token, either:

1. buffer the final assistant message and render once, or
2. keep streaming plain and render only completed responses.

Prefer the lower-risk option that fits the current output flow.

## Files to Inspect / Modify

Likely:

```text
src/d2c/main.py
tests/test_repl_ux.py
```

Optional:

```text
src/d2c/markdown_render.py
tests/test_markdown_render.py
README.md
CHANGELOG.md
plans/phase73-repl-markdown-rendering.md
```

## Tests

Add tests for:

1. Heading rendering produces styled formatted text.
2. Bullets and numbered lists preserve readable structure.
3. Fenced code blocks preserve indentation and content.
4. Inline code is styled without changing text content.
5. Links preserve visible URL.
6. Malformed/unclosed fences do not crash.
7. Headless output path remains plain text.

Keep tests independent of terminal capabilities; assert formatted-text tokens or
captured plain fallback, not exact ANSI escape codes.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_repl_ux.py tests/test_markdown_render.py
```

Do not run the full release gate unless explicitly requested.

## Acceptance Criteria

- Interactive assistant responses render common Markdown legibly.
- Headless, SDK, MCP, and eval outputs are unchanged.
- Renderer has no network/file side effects.
- Malformed Markdown falls back safely or remains readable.
- Tests cover the supported Markdown subset.
- Fast checks pass.

## Expected Outcome

Interactive sessions become substantially easier to scan. The REPL can display
the Markdown that models already produce without requiring a full TUI rewrite or
changing non-interactive interfaces.
