# Phase 10: DeepSeek as Coding Model

## Goal

Use DeepSeek exclusively as the model backend. No Anthropic models. DeepSeek supports
both OpenAI-compatible and Anthropic-compatible API formats — pick one SDK to use.

## DeepSeek API Facts

| Property | Value |
|---|---|
| Base URL (OpenAI format) | `https://api.deepseek.com` |
| Base URL (Anthropic format) | `https://api.deepseek.com/anthropic` |
| Auth | `Authorization: Bearer <api_key>` |
| Models | `deepseek-v4-pro`, `deepseek-v4-flash`, `deepseek-chat`, `deepseek-reasoner` |
| Context | 128K tokens |
| Tool calling (OpenAI) | Standard `tools` param, OpenAI format |
| Tool calling (Anthropic) | Standard `tools` param, Anthropic format |

## Design Decision: Which SDK?

Two options:

| Option | SDK | Base URL | Tool format |
|---|---|---|---|
| **A: OpenAI SDK** | `openai` | `https://api.deepseek.com` | OpenAI function-calling |
| **B: Anthropic SDK** | `anthropic` | `https://api.deepseek.com/anthropic` | Anthropic tool_use |

**Pick Option B (Anthropic SDK)** — our tools already use Anthropic's tool schema format
(`to_api_format()` returns Anthropic-compatible dicts). Using the Anthropic SDK means
zero message/tool schema conversion. Just point the existing `anthropic` SDK at DeepSeek's
Anthropic-compatible endpoint.

## Changes Required

### 1. Config (`config.py`) — model selection

```python
@dataclass
class Config:
    model: str = "deepseek-v4-pro"
    deepseek_api_key: str | None = None   # or env DEEPSEEK_API_KEY
    deepseek_base_url: str = "https://api.deepseek.com/anthropic"
```

### 2. Model client init — point Anthropic SDK at DeepSeek

```python
# In loop.py (or a thin helper)
import anthropic

client = anthropic.Anthropic(
    api_key=config.deepseek_api_key or os.environ["DEEPSEEK_API_KEY"],
    base_url=config.deepseek_base_url,  # "https://api.deepseek.com/anthropic"
)

response = client.messages.create(
    model=config.model,
    system=system_prompt,
    messages=messages,
    tools=tool_schemas,
    max_tokens=8192,
)
```

### 3. Remove Anthropic model references

- Replace `claude-sonnet-4-6` default with `deepseek-v4-pro`
- No model switching — DeepSeek only

### 4. `pyproject.toml`

```toml
dependencies = [
    "anthropic>=0.39.0",   # for Anthropic SDK pointed at DeepSeek
    # "openai" — not needed if using Anthropic SDK path
    ...
]
```

## No Changes Needed

Everything else is unchanged — the tool schemas, message format, streaming, and
tool result handling are all Anthropic-format already. DeepSeek's Anthropic-compatible
endpoint accepts the exact same format.

## What If We Pick OpenAI SDK Instead?

If the Anthropic-compatible endpoint has issues, switching to the OpenAI SDK requires:

1. Message format conversion — our `messages: list[dict]` with content blocks → OpenAI `[{role, content, tool_calls}]`
2. Tool schema conversion — `{name, description, input_schema}` → `{type: "function", function: {name, description, parameters}}`
3. Tool call parsing — OpenAI returns `tool_calls` array on the message, not `content` blocks

This is more code. Option B (Anthropic SDK) is zero-effort by comparison.

## Edge Cases

| Condition | Handling |
|---|---|
| `DEEPSEEK_API_KEY` not set | Error at startup |
| DeepSeek returns Anthropic-format tool_use | Parsed identically to Claude responses |
| Streaming | Anthropic SSE format — same as current |
| Rate limiting | Standard retry (Anthropic SDK handles 429) |
| Reasoning/thinking tokens | DeepSeek may return `thinking` content blocks — preserve them |
