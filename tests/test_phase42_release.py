"""Phase 42: release-readiness regressions.

Guards the fresh-install contract: bundled skill data ships and loads, and
config validation gives clear (non-secret) messages for missing/invalid setup.
"""

from d2c.config import Config
from d2c.skills.loader import load_bundled_skills


def test_bundled_commit_skill_is_packaged_and_loads():
    # Guards the pyproject package-data entry: commit.md must be discoverable
    # from the installed package (glob over the skills dir), not just in a
    # source checkout.
    names = [s.name for s in load_bundled_skills()]
    assert "commit" in names


def test_bundled_skill_has_description_and_prompt():
    commit = next(s for s in load_bundled_skills() if s.name == "commit")
    assert commit.description
    assert commit.prompt.strip()


def test_config_validate_flags_missing_api_key():
    issues = Config(deepseek_api_key=None).validate()
    assert any("DEEPSEEK_API_KEY" in i for i in issues)


def test_config_validate_flags_unknown_model():
    issues = Config(deepseek_api_key="k", model="not-a-real-model").validate()
    assert any("not a recognized" in i.lower() or "not a recognized deepseek" in i.lower()
               for i in issues)


def test_config_validate_clean_when_configured():
    issues = Config(deepseek_api_key="sk-x", model="deepseek-v4-pro").validate()
    assert issues == []
