"""Tests for Phase 9: SkillTool, WebFetch, WebSearch, and skills loader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from d2c.tools import PermissionCategory


# ── Skills Loader tests ────────────────────────────────────────────────

class TestSkillDefinition:
    def test_skill_definition_fields(self):
        from d2c.skills.loader import SkillDefinition
        sd = SkillDefinition(
            name="my-skill",
            description="Does something",
            prompt="You are a helpful skill.",
        )
        assert sd.name == "my-skill"
        assert sd.description == "Does something"
        assert sd.prompt == "You are a helpful skill."
        assert sd.source == "bundled"
        assert sd.args_schema is None

    def test_skill_definition_user_source(self):
        from d2c.skills.loader import SkillDefinition
        sd = SkillDefinition(name="test", description="d", prompt="p", source="user")
        assert sd.source == "user"


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        from d2c.skills.loader import parse_frontmatter
        meta, body = parse_frontmatter("Just body text")
        assert meta == {}
        assert body == "Just body text"

    def test_valid_frontmatter(self):
        from d2c.skills.loader import parse_frontmatter
        text = """---
description: "A test skill"
args: "-m message"
---
You are a test skill."""
        meta, body = parse_frontmatter(text)
        assert meta["description"] == "A test skill"
        # strip('"') on "-m message" yields "-m message" without outer quotes
        assert meta["args"] == "-m message"
        assert body == "You are a test skill."

    def test_missing_closing_delimiter(self):
        from d2c.skills.loader import parse_frontmatter
        text = """---
name: Incomplete
Body."""
        meta, body = parse_frontmatter(text)
        assert meta == {}


class TestLoadBundledSkills:
    def test_loads_commit_skill(self):
        from d2c.skills.loader import load_bundled_skills
        skills = load_bundled_skills()
        names = {s.name for s in skills}
        assert "commit" in names

    def test_commit_skill_has_prompt(self):
        from d2c.skills.loader import load_bundled_skills
        skills = load_bundled_skills()
        commit = next(s for s in skills if s.name == "commit")
        assert len(commit.prompt) > 50
        assert "git status" in commit.prompt

    def test_bundled_skills_have_bundled_source(self):
        from d2c.skills.loader import load_bundled_skills
        skills = load_bundled_skills()
        for s in skills:
            assert s.source == "bundled"


class TestLoadUserSkills:
    def test_empty_when_no_dir(self, tmp_path):
        from d2c.skills.loader import load_user_skills
        skills = load_user_skills(tmp_path)
        assert skills == []

    def test_loads_user_skills_from_dir(self, tmp_path):
        from d2c.skills.loader import load_user_skills
        skills_dir = tmp_path / ".d2c" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "my-skill.md").write_text("""---
description: "My custom skill"
---
Custom skill prompt body.""")

        skills = load_user_skills(tmp_path)
        assert len(skills) == 1
        assert skills[0].name == "my-skill"
        assert skills[0].source == "user"
        assert "Custom skill prompt body" in skills[0].prompt

    def test_loads_multiple_user_skills(self, tmp_path):
        from d2c.skills.loader import load_user_skills
        skills_dir = tmp_path / ".d2c" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "a.md").write_text("# Skill A")
        (skills_dir / "b.md").write_text("# Skill B")

        skills = load_user_skills(tmp_path)
        assert len(skills) == 2


class TestLoadAllSkills:
    def test_includes_bundled_and_user(self, tmp_path):
        from d2c.skills.loader import load_all_skills
        skills_dir = tmp_path / ".d2c" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "my-skill.md").write_text("""---
description: "User skill"
---
User content.""")

        skills = load_all_skills(tmp_path)
        names = {s.name for s in skills}
        assert "commit" in names  # bundled
        assert "my-skill" in names  # user

    def test_user_overrides_bundled(self, tmp_path):
        from d2c.skills.loader import load_all_skills
        skills_dir = tmp_path / ".d2c" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "commit.md").write_text("""---
description: "Custom commit"
---
Custom commit prompt.""")

        skills = load_all_skills(tmp_path)
        commit_skills = [s for s in skills if s.name == "commit"]
        assert len(commit_skills) == 1
        assert commit_skills[0].source == "user"
        assert "Custom commit prompt" in commit_skills[0].prompt


# ── SkillTool tests ────────────────────────────────────────────────────

class TestSkillTool:
    @pytest.mark.asyncio
    async def test_skill_tool_basic(self):
        from d2c.skills.loader import SkillDefinition
        from d2c.tools.skill_tool import SkillTool

        sd = SkillDefinition(
            name="test-skill",
            description="A test",
            prompt="You are a test skill. Be helpful.",
        )
        tool = SkillTool(skills=[sd])
        result = await tool.execute(skill="test-skill")
        assert result.error is False
        assert "You are a test skill" in result.output
        assert result.metadata["skill_name"] == "test-skill"
        assert result.metadata["action"] == "inject_into_context"

    @pytest.mark.asyncio
    async def test_skill_tool_unknown_skill(self):
        from d2c.tools.skill_tool import SkillTool
        tool = SkillTool(skills=[])
        result = await tool.execute(skill="nonexistent")
        assert result.error is True
        assert "Unknown skill" in result.output

    @pytest.mark.asyncio
    async def test_skill_tool_with_args(self):
        from d2c.skills.loader import SkillDefinition
        from d2c.tools.skill_tool import SkillTool

        sd = SkillDefinition(name="greet", description="Greets", prompt="Hello!")
        tool = SkillTool(skills=[sd])
        result = await tool.execute(skill="greet", args="--name World")
        assert result.error is False
        assert "Hello!" in result.output
        assert "--name World" in result.output

    @pytest.mark.asyncio
    async def test_skill_tool_lazy_loads(self):
        """SkillTool loads skills lazily if none provided at construction."""
        from d2c.tools.skill_tool import SkillTool
        tool = SkillTool()  # no skills, will lazy load

        # Should find bundled commit skill
        result = await tool.execute(skill="commit")
        assert result.error is False
        assert "git status" in result.output

    @pytest.mark.asyncio
    async def test_skill_tool_metadata(self):
        from d2c.skills.loader import SkillDefinition
        from d2c.tools.skill_tool import SkillTool

        sd = SkillDefinition(name="meta-test", description="d", prompt="p", source="user")
        tool = SkillTool(skills=[sd])
        result = await tool.execute(skill="meta-test")
        assert result.metadata["source"] == "user"

    def test_skill_tool_attributes(self):
        from d2c.tools.skill_tool import SkillTool
        tool = SkillTool()
        assert tool.name == "Skill"
        assert tool.category == PermissionCategory.META
        assert tool.is_concurrent_safe is True

    def test_skill_tool_api_format(self):
        from d2c.tools.skill_tool import SkillTool
        tool = SkillTool()
        fmt = tool.to_api_format()
        assert fmt["name"] == "Skill"
        assert "input_schema" in fmt
        assert "skill" in fmt["input_schema"]["properties"]


# ── WebFetchTool tests ─────────────────────────────────────────────────

class TestWebFetchTool:
    @pytest.mark.asyncio
    async def test_missing_url(self):
        from d2c.tools.web_fetch import WebFetchTool
        tool = WebFetchTool()
        result = await tool.execute()
        assert result.error is True
        assert "URL is required" in result.output

    @pytest.mark.asyncio
    async def test_invalid_url_no_scheme(self):
        from d2c.tools.web_fetch import WebFetchTool
        tool = WebFetchTool()
        result = await tool.execute(url="example.com")
        assert result.error is True
        assert "Invalid URL" in result.output

    @pytest.mark.asyncio
    async def test_unsupported_scheme(self):
        from d2c.tools.web_fetch import WebFetchTool
        tool = WebFetchTool()
        result = await tool.execute(url="ftp://files.example.com/data")
        assert result.error is True
        assert "Unsupported URL scheme" in result.output

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        from d2c.tools.web_fetch import WebFetchTool

        tool = WebFetchTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        # httpx imported lazily inside execute(), patch at module level
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool.execute(url="https://example.com")

        assert result.error is False
        assert "Hello World" in result.output
        assert result.metadata["status_code"] == 200
        assert result.metadata["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_truncates_long_content(self):
        from d2c.tools.web_fetch import WebFetchTool

        tool = WebFetchTool()

        long_text = "A" * 15_000
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        mock_response.text = long_text
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool.execute(url="https://example.com", max_length=100)

        assert result.error is False
        assert "Truncated" in result.output
        assert len(result.output) <= 200  # 100 + truncation notice

    @pytest.mark.asyncio
    async def test_http_error(self):
        from d2c.tools.web_fetch import WebFetchTool

        tool = WebFetchTool()

        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=mock_response
        ))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool.execute(url="https://example.com/404")

        assert result.error is True
        assert "404" in result.output

    @pytest.mark.asyncio
    async def test_timeout(self):
        from d2c.tools.web_fetch import WebFetchTool

        tool = WebFetchTool()

        import httpx
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool.execute(url="https://example.com")

        assert result.error is True
        assert "timed out" in result.output.lower()

    @pytest.mark.asyncio
    async def test_html_to_text_strips_tags(self):
        from d2c.tools.web_fetch import _html_to_text
        text = _html_to_text(
            "<html><head><script>alert('xss')</script></head>"
            "<body><h1>Title</h1><p>Content</p></body></html>"
        )
        assert "Title" in text
        assert "Content" in text
        assert "alert" not in text
        assert "<script>" not in text

    @pytest.mark.asyncio
    async def test_html_to_text_strips_comments(self):
        from d2c.tools.web_fetch import _html_to_text
        text = _html_to_text("<!-- secret --><p>visible</p>")
        assert "visible" in text
        assert "secret" not in text

    def test_webfetch_tool_attributes(self):
        from d2c.tools.web_fetch import WebFetchTool
        tool = WebFetchTool()
        assert tool.name == "WebFetch"
        assert tool.category == PermissionCategory.READ
        assert tool.is_concurrent_safe is True


# ── WebSearchTool tests ────────────────────────────────────────────────

class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_missing_query(self):
        from d2c.tools.web_search import WebSearchTool
        tool = WebSearchTool()
        result = await tool.execute()
        assert result.error is True
        assert "query" in result.output.lower()

    @pytest.mark.asyncio
    async def test_not_configured_message(self):
        from d2c.tools.web_search import WebSearchTool
        tool = WebSearchTool()
        result = await tool.execute(query="test query")
        assert result.error is False
        assert "not configured" in result.output
        assert result.metadata["configured"] is False
        assert "test query" in result.output

    @pytest.mark.asyncio
    async def test_max_results_clamped(self):
        from d2c.tools.web_search import WebSearchTool
        tool = WebSearchTool()
        result = await tool.execute(query="test", max_results=50)
        assert result.error is False
        # max_results is clamped to 20 internally, so 50 → 20
        assert "20" in result.output

    def test_websearch_tool_attributes(self):
        from d2c.tools.web_search import WebSearchTool
        tool = WebSearchTool()
        assert tool.name == "WebSearch"
        assert tool.category == PermissionCategory.READ
        assert tool.is_concurrent_safe is True
