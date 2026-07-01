"""Phase 56: ConfigInfo + PackageInfo + CodeSymbols tools (tool breadth batch 3)."""

import pytest

from d2c.tools import PermissionCategory
from d2c.tools.code_symbols import CodeSymbolsTool
from d2c.tools.config_info import ConfigInfoTool
from d2c.tools.package_info import PackageInfoTool

# ── Schemas / categories / pool registration ───────────────────────────


def test_categories_and_schemas():
    for tool_cls in (ConfigInfoTool, PackageInfoTool, CodeSymbolsTool):
        t = tool_cls()
        assert t.category == PermissionCategory.READ
        assert t.is_concurrent_safe is True
    assert "file_path" in CodeSymbolsTool().input_schema["properties"]
    assert "path" in PackageInfoTool().input_schema["properties"]


@pytest.mark.asyncio
async def test_new_tools_registered_in_pool(trusted_gate):
    from d2c.tools.pool import Config, assembleToolPool

    names = {t.name for t in await assembleToolPool(Config())}
    assert {"ConfigInfo", "PackageInfo", "CodeSymbols"} <= names


# ── ConfigInfo ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_configinfo_fields(tmp_dir, monkeypatch, trusted_gate):
    monkeypatch.setenv("D2C_MODEL", "deepseek-chat")
    monkeypatch.setenv("D2C_WEBSEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("D2C_SANDBOX", "1")
    res = await ConfigInfoTool(cwd=tmp_dir, permission_mode="acceptEdits").execute()
    m = res.metadata
    for k in (
        "cwd",
        "model",
        "permission_mode",
        "trusted",
        "sandbox_enabled",
        "audit_log_enabled",
        "websearch_provider",
        "websearch_configured",
        "cost_estimates_disabled",
    ):
        assert k in m
    assert m["model"] == "deepseek-chat"
    assert m["permission_mode"] == "acceptEdits"
    assert m["websearch_provider"] == "tavily"
    assert m["sandbox_enabled"] is True
    assert str(tmp_dir) == m["cwd"]
    assert "permission mode: acceptEdits" in res.output


@pytest.mark.asyncio
async def test_configinfo_never_exposes_secrets(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-appear-123")
    monkeypatch.setenv("D2C_WEBSEARCH_API_KEY", "tvly-should-not-appear")
    res = await ConfigInfoTool().execute()
    blob = res.output + str(res.metadata)
    assert "sk-should-not-appear-123" not in blob
    assert "tvly-should-not-appear" not in blob
    assert "api_key" not in {k.lower() for k in res.metadata}
    # Presence, not the value, is what's reported.
    assert res.metadata["websearch_configured"] is True


@pytest.mark.asyncio
async def test_configinfo_untrusted_workspace_reports_untrusted(tmp_dir, untrusted_gate):
    res = await ConfigInfoTool(cwd=tmp_dir).execute()
    assert res.metadata["trusted"] is False


# ── PackageInfo: pyproject.toml ────────────────────────────────────────


@pytest.mark.asyncio
async def test_packageinfo_parses_pyproject(tmp_dir):
    (tmp_dir / "pyproject.toml").write_text(
        """
[project]
name = "widget"
version = "1.2.3"
description = "A widget."
dependencies = ["httpx", "anyio"]

[project.scripts]
run-widget = "widget.main:cli"
"""
    )
    res = await PackageInfoTool(cwd=tmp_dir).execute()
    assert not res.error
    assert res.metadata["manifest"] == "pyproject.toml"
    assert res.metadata["name"] == "widget"
    assert res.metadata["version"] == "1.2.3"
    assert res.metadata["dependency_count"] == 2
    assert "widget" in res.output and "1.2.3" in res.output
    assert "run-widget" in res.output  # script name listed
    assert "widget.main:cli" not in res.output  # entry-point target not printed


@pytest.mark.asyncio
async def test_packageinfo_poetry_style_fallback(tmp_dir):
    (tmp_dir / "pyproject.toml").write_text(
        """
[tool.poetry]
name = "poetry-widget"
version = "0.1.0"
"""
    )
    res = await PackageInfoTool(cwd=tmp_dir).execute()
    assert res.metadata["name"] == "poetry-widget"
    assert res.metadata["version"] == "0.1.0"


# ── PackageInfo: package.json ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_packageinfo_parses_package_json(tmp_dir):
    (tmp_dir / "package.json").write_text(
        '{"name": "app", "version": "2.0.0", '
        '"dependencies": {"react": "^18.0.0"}, '
        '"devDependencies": {"vitest": "^1.0.0"}, '
        '"scripts": {"build": "vite build"}}'
    )
    res = await PackageInfoTool(cwd=tmp_dir).execute()
    assert res.metadata["manifest"] == "package.json"
    assert res.metadata["name"] == "app"
    assert res.metadata["dependency_count"] == 2
    assert "build" in res.output


# ── PackageInfo: requirements.txt fallback ──────────────────────────────


@pytest.mark.asyncio
async def test_packageinfo_requirements_txt_fallback(tmp_dir):
    (tmp_dir / "requirements.txt").write_text("# comment\nrequests>=2\nflask\n\n")
    res = await PackageInfoTool(cwd=tmp_dir).execute()
    assert res.metadata["manifest"] == "requirements.txt"
    assert res.metadata["dependency_count"] == 2


# ── PackageInfo: malformed / missing ────────────────────────────────────


@pytest.mark.asyncio
async def test_packageinfo_malformed_toml_is_reported_not_raised(tmp_dir):
    (tmp_dir / "pyproject.toml").write_text("this is not [ valid toml")
    res = await PackageInfoTool(cwd=tmp_dir).execute()
    assert res.error
    assert "Error parsing pyproject.toml" in res.output


@pytest.mark.asyncio
async def test_packageinfo_malformed_json_is_reported_not_raised(tmp_dir):
    (tmp_dir / "package.json").write_text("{not valid json")
    res = await PackageInfoTool(cwd=tmp_dir).execute()
    assert res.error
    assert "Error parsing package.json" in res.output


@pytest.mark.asyncio
async def test_packageinfo_no_manifest_found(tmp_dir):
    res = await PackageInfoTool(cwd=tmp_dir).execute()
    assert not res.error
    assert res.metadata["found"] is False


@pytest.mark.asyncio
async def test_packageinfo_dependency_truncation(tmp_dir):
    deps = ", ".join(f'"dep{i}"' for i in range(50))
    (tmp_dir / "pyproject.toml").write_text(
        f'[project]\nname = "big"\nversion = "1.0"\ndependencies = [{deps}]\n'
    )
    res = await PackageInfoTool(cwd=tmp_dir).execute()
    assert res.metadata["dependency_count"] == 50
    assert res.metadata["dependencies_truncated"] is True
    assert "truncated" in res.output


@pytest.mark.asyncio
async def test_packageinfo_rejects_relative_path(tmp_dir):
    res = await PackageInfoTool(cwd=tmp_dir).execute(path="relative/dir")
    assert res.error and "absolute" in res.output.lower()


# ── CodeSymbols ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_codesymbols_extracts_classes_functions_imports(tmp_dir):
    f = tmp_dir / "mod.py"
    f.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "def top_level():\n"
        "    pass\n"
        "\n"
        "async def top_level_async():\n"
        "    pass\n"
        "\n"
        "class Widget:\n"
        "    def method(self):\n"
        "        pass\n"
        "\n"
        "    async def amethod(self):\n"
        "        pass\n"
    )
    res = await CodeSymbolsTool().execute(file_path=str(f))
    assert not res.error
    kinds = {s["name"]: s["kind"] for s in res.metadata["symbols"]}
    assert kinds["os"] == "import"
    assert kinds["pathlib.Path"] == "import"
    assert kinds["top_level"] == "function"
    assert kinds["top_level_async"] == "async function"
    assert kinds["Widget"] == "class"
    assert kinds["Widget.method"] == "method (function)"
    assert kinds["Widget.amethod"] == "method (async function)"
    lines = {s["name"]: s["line"] for s in res.metadata["symbols"]}
    assert lines["top_level"] == 4


@pytest.mark.asyncio
async def test_codesymbols_include_imports_false(tmp_dir):
    f = tmp_dir / "mod.py"
    f.write_text("import os\n\ndef f():\n    pass\n")
    res = await CodeSymbolsTool().execute(file_path=str(f), include_imports=False)
    names = {s["name"] for s in res.metadata["symbols"]}
    assert "os" not in names
    assert "f" in names


@pytest.mark.asyncio
async def test_codesymbols_syntax_error_reported_not_raised(tmp_dir):
    f = tmp_dir / "broken.py"
    f.write_text("def f(:\n    pass\n")
    res = await CodeSymbolsTool().execute(file_path=str(f))
    assert res.error
    assert "Error parsing" in res.output


@pytest.mark.asyncio
async def test_codesymbols_rejects_non_python_file(tmp_dir):
    f = tmp_dir / "notes.txt"
    f.write_text("hello")
    res = await CodeSymbolsTool().execute(file_path=str(f))
    assert res.error
    assert "Python files" in res.output


@pytest.mark.asyncio
async def test_codesymbols_rejects_relative_path(tmp_dir):
    res = await CodeSymbolsTool().execute(file_path="relative.py")
    assert res.error and "absolute" in res.output.lower()


@pytest.mark.asyncio
async def test_codesymbols_missing_file(tmp_dir):
    res = await CodeSymbolsTool().execute(file_path=str(tmp_dir / "nope.py"))
    assert res.error and "not a file" in res.output.lower()


@pytest.mark.asyncio
async def test_codesymbols_truncation(tmp_dir):
    f = tmp_dir / "many.py"
    f.write_text("\n".join(f"def f{i}():\n    pass" for i in range(350)))
    res = await CodeSymbolsTool().execute(file_path=str(f))
    assert res.metadata["symbol_count"] == 350
    assert res.metadata["truncated"] is True
    assert "truncated" in res.output


@pytest.mark.asyncio
async def test_codesymbols_empty_file_no_symbols(tmp_dir):
    f = tmp_dir / "empty.py"
    f.write_text("# just a comment\n")
    res = await CodeSymbolsTool().execute(file_path=str(f))
    assert not res.error
    assert res.metadata["symbol_count"] == 0
