# Phase 28: Real BPE Tokenizer Integration (Context Depth)

**Paper Reference:** Section 3.6, 7.3 — "the context window is the binding resource constraint... five-layer compaction pipeline manages context pressure."

**Priority:** HIGH (Accuracy & Safety)

## Rationale

The current token estimation in `d2c` relies on a simple character divisor (`total_chars / 3.5`). This is highly inaccurate, especially for JSON-wrapped tool parameters, programming code, and binary outputs. 

To improve robustness, we will integrate a real Byte-Pair Encoding (BPE) tokenizer (`tiktoken` using the `cl100k_base` encoding, which matches Anthropic's tokenization layout). This ensures that the context pressure threshold triggers compaction at exactly the right moment, eliminating random API context window overflows.

---

## Files to Create/Modify

1. MODIFY `pyproject.toml` — add `tiktoken` to project dependencies
2. MODIFY `src/d2c/context.py` — replace `estimate_tokens` with BPE token counter
3. MODIFY `src/d2c/compact.py` — update references to `estimate_tokens`
4. CREATE `tests/test_bpe_tokenizer.py` — test precise BPE counting

---

## Key Design

### 1. Adding Dependencies
Add `tiktoken` to the core dependencies block in `pyproject.toml`:
```toml
dependencies = [
    "anthropic>=0.39.0",
    "pydantic>=2.0.0",
    "httpx>=0.27.0",
    "pyyaml>=6.0",
    "pymupdf>=1.23.0",
    "tiktoken>=0.7.0",  # Added for precise token counting
]
```

### 2. Message Token Counter (`src/d2c/context.py`)
We will implement an exact token counting function. Anthropic messages format maps to BPE tokens plus fixed overheads per message (role metadata, name tags).

```python
import tiktoken
import json

def estimate_tokens(messages: list[dict], chars_per_token: float = 3.5) -> int:
    """Precise BPE token counting using cl100k_base.
    
    Falls back to character-based heuristic if tiktoken is not available.
    """
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception:
        # Fallback to simple heuristic
        return _fallback_estimate_tokens(messages, chars_per_token)

    num_tokens = 0
    for message in messages:
        # Anthropic message structure overhead (approx 4 tokens per message)
        num_tokens += 4
        
        role = message.get("role", "")
        content = message.get("content", "")
        
        num_tokens += len(encoding.encode(role))
        
        if isinstance(content, str):
            num_tokens += len(encoding.encode(content))
        elif isinstance(content, list):
            # Message contains structured blocks (e.g. tool_use, text blocks)
            # Serialize content list to inspect block text
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    num_tokens += len(encoding.encode(block_type))
                    
                    if block_type == "text":
                        num_tokens += len(encoding.encode(block.get("text", "")))
                    elif block_type == "tool_use":
                        num_tokens += len(encoding.encode(block.get("name", "")))
                        num_tokens += len(encoding.encode(json.dumps(block.get("input", {}))))
                    elif block_type == "tool_result":
                        num_tokens += len(encoding.encode(block.get("tool_use_id", "")))
                        num_tokens += len(encoding.encode(str(block.get("content", ""))))
        else:
            num_tokens += len(encoding.encode(str(content)))
            
    # Add conversation framing overhead (approx 3 tokens for assistant start)
    num_tokens += 3
    return num_tokens

def _fallback_estimate_tokens(messages: list[dict], chars_per_token: float) -> int:
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += len(json.dumps(content))
        else:
            total += len(str(content))
    return int(total / chars_per_token)
```

---

## Edge Cases

* **Tiktoken loading failure**: If `tiktoken` fails to load the model file (e.g. offline environments where it cannot download the encoder file on first run), the parser catches the error and falls back to character-based token estimation.
* **Large tool output formats**: Large binary/hex attachments in content blocks will be converted to string representation for safe tokenization.

---

## Tests

Verify the following:
* `test_bpe_counting_text_only`: Exact token count for basic string messages matches expected `cl100k_base` output.
* `test_bpe_counting_tool_use`: Verifies tokens for structured tool call blocks.
* `test_bpe_counting_tool_result`: Verifies token counts for tool output results.
* `test_bpe_counting_fallback_on_error`: Verifies system falls back to character division safely if `tiktoken` encounters an import error.
