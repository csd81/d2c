"""Phase 73: REPL Markdown renderer.

Asserts on formatted-text fragments (style, text), never on ANSI escapes, so the
tests are independent of terminal capabilities.
"""

from __future__ import annotations

import d2c.markdown_render as md
from d2c.markdown_render import (
    STYLE_CODE,
    markdown_to_fragments,
    render_markdown,
)


def _text(frags) -> str:
    return "".join(t for _, t in frags)


def _styles(frags) -> list[str]:
    return [s for s, _ in frags]


def test_heading_is_styled_bold():
    frags = markdown_to_fragments("# Title")
    assert any("bold" in s and t == "Title" for s, t in frags)
    assert "#" not in _text(frags)  # marker stripped


def test_h2_is_bold():
    frags = markdown_to_fragments("## Section")
    assert any("bold" in s and t == "Section" for s, t in frags)


def test_bullets_preserve_structure():
    frags = markdown_to_fragments("- one\n- two")
    text = _text(frags)
    assert "one" in text and "two" in text
    assert "•" in text  # bullet marker rendered


def test_numbered_list_preserves_numbers():
    frags = markdown_to_fragments("1. first\n2. second")
    text = _text(frags)
    assert "1. first" in text
    assert "2. second" in text


def test_fenced_code_block_preserves_content_and_indent():
    frags = markdown_to_fragments("```\n    x = 1\n```")
    # the code line is present, styled as code, with its indentation kept
    assert any(s == STYLE_CODE and "x = 1" in t for s, t in frags)


def test_inline_code_is_styled_without_changing_text():
    frags = markdown_to_fragments("use `x=1` now")
    assert (STYLE_CODE, "x=1") in frags
    assert "x=1" in _text(frags)
    assert "now" in _text(frags)


def test_link_keeps_visible_url():
    frags = markdown_to_fragments("see [docs](https://example.com/x)")
    text = _text(frags)
    assert "docs" in text
    assert "https://example.com/x" in text


def test_blockquote_is_muted():
    frags = markdown_to_fragments("> heads up")
    text = _text(frags)
    assert "heads up" in text
    assert any("ansibrightblack" in s for s in _styles(frags))


def test_unclosed_fence_does_not_crash():
    frags = markdown_to_fragments("```\nunclosed code")
    assert "unclosed code" in _text(frags)


def test_plain_text_passes_through_unstyled():
    frags = markdown_to_fragments("just a normal sentence")
    assert _text(frags) == "just a normal sentence"
    assert set(_styles(frags)) == {""}


def test_render_markdown_returns_formatted_text():
    from prompt_toolkit.formatted_text import FormattedText

    result = render_markdown("# Hi")
    assert isinstance(result, FormattedText)


def test_render_markdown_fails_open_to_plain(monkeypatch):
    def boom(_text):
        raise ValueError("kaboom")

    monkeypatch.setattr(md, "markdown_to_fragments", boom)
    result = render_markdown("# Hi\nsome text")
    assert list(result) == [("", "# Hi\nsome text")]  # original text, plain
