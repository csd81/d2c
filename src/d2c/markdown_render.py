"""Phase 73: a tiny, dependency-free Markdown renderer for the interactive REPL.

Renders a pragmatic subset — headings, bullet/numbered lists, fenced and inline
code, links, and blockquotes — to ``prompt_toolkit`` FormattedText (a list of
``(style, text)`` fragments). It is display-only and has NO side effects: it
never executes HTML, fetches links, or reads files, and on any parsing error it
falls back to the original text as plain output.

Not a CommonMark implementation — tables and nested/complex constructs render as
readable plain text. Only the interactive REPL uses this; headless, SDK, MCP,
and eval output paths stay plain.
"""

from __future__ import annotations

import re

from prompt_toolkit.formatted_text import FormattedText

Fragment = tuple[str, str]

# Styles are plain prompt_toolkit style strings (no Style registry needed), so
# they render in a normal terminal and are easy to assert on in tests.
STYLE_PLAIN = ""
STYLE_H1 = "bold ansimagenta"
STYLE_H = "bold"
STYLE_CODE = "ansicyan"
STYLE_QUOTE = "ansibrightblack"
STYLE_LINK = "ansiblue underline"
STYLE_BULLET = "bold"

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_NEWLINE: Fragment = (STYLE_PLAIN, "\n")


def _inline(text: str) -> list[Fragment]:
    """Style inline code and links inside a single line. Links render as
    ``text (url)`` so the destination stays visible. Text content is preserved."""
    frags: list[Fragment] = []
    pos = 0
    while pos < len(text):
        code_m = _INLINE_CODE.search(text, pos)
        link_m = _LINK.search(text, pos)
        matches = [m for m in (code_m, link_m) if m is not None]
        if not matches:
            frags.append((STYLE_PLAIN, text[pos:]))
            break
        m = min(matches, key=lambda x: x.start())
        if m.start() > pos:
            frags.append((STYLE_PLAIN, text[pos : m.start()]))
        if m is code_m:
            frags.append((STYLE_CODE, m.group(1)))
        else:
            frags.append((STYLE_LINK, m.group(1)))
            frags.append((STYLE_PLAIN, f" ({m.group(2)})"))
        pos = m.end()
    return frags or [(STYLE_PLAIN, text)]


def markdown_to_fragments(text: str) -> list[Fragment]:
    """Convert Markdown ``text`` into a list of ``(style, text)`` fragments."""
    out: list[Fragment] = []
    in_code = False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            out.append((STYLE_QUOTE, "  ┄┄┄"))  # ┄┄┄ fence marker
            out.append(_NEWLINE)
            continue
        if in_code:
            out.append((STYLE_CODE, "    " + line))
            out.append(_NEWLINE)
            continue

        heading = _HEADING.match(line)
        if heading:
            style = STYLE_H1 if len(heading.group(1)) == 1 else STYLE_H
            out.append((style, heading.group(2)))
            out.append(_NEWLINE)
            continue

        bullet = _BULLET.match(line)
        if bullet:
            out.append((STYLE_BULLET, f"{bullet.group(1)}  • "))  # • bullet
            out.extend(_inline(bullet.group(2)))
            out.append(_NEWLINE)
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            out.append((STYLE_PLAIN, f"{numbered.group(1)}  {numbered.group(2)}. "))
            out.extend(_inline(numbered.group(3)))
            out.append(_NEWLINE)
            continue

        quote = _QUOTE.match(line)
        if quote:
            out.append((STYLE_QUOTE, "  ▎ "))  # ▎ bar
            # Muted the whole quoted line, keeping code/link accents.
            out.extend(
                (STYLE_QUOTE if s == STYLE_PLAIN else s, t) for s, t in _inline(quote.group(1))
            )
            out.append(_NEWLINE)
            continue

        out.extend(_inline(line))
        out.append(_NEWLINE)

    if out and out[-1] == _NEWLINE:
        out.pop()  # no dangling blank line
    return out


def render_markdown(text: str) -> FormattedText:
    """Render ``text`` to FormattedText, failing open to plain text on any error."""
    try:
        return FormattedText(markdown_to_fragments(text))
    except Exception:
        return FormattedText([(STYLE_PLAIN, text)])
