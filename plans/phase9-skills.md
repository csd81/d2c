# Phase 9: SkillTool, WebFetch, WebSearch

## Files

- `src/d2c/skills/loader.py` — load_bundled_skills(), load_user_skills(), SkillDefinition
- `src/d2c/tools/skill_tool.py` — SkillTool (meta-tool)
- `src/d2c/tools/web_fetch.py` — WebFetchTool
- `src/d2c/tools/web_search.py` — WebSearchTool

## SkillTool

- Skills advertised via descriptions (low context cost)
- Full prompt injected only on invocation
- Key difference from AgentTool: injects into current context (not new isolated one)
- Bundled skills from package; user skills from `.d2c/skills/*.md`

## Edge Cases

- Unknown skill → error with available list
- WebSearch no results → appropriate empty response
- WebFetch redirect loop → max redirects
