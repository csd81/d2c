# Phase 25: KAIROS Persistent Background Agent (Feature-Gated)

**Paper Reference:** Section 11.6 — "Feature-gated KAIROS system: persistent background
agent with tick-based heartbeats."

**Priority:** LOW

## Rationale

The paper's most forward-looking feature. A background agent that periodically wakes,
checks for tasks, and acts autonomously when the user is idle. Key design elements:
terminal focus awareness (maximizes autonomous action when user is away), economic
throttling via SleepTool (each wake-up costs an API call), and prompt cache awareness
(cache expires after 5 minutes of inactivity). This is speculative and feature-gated.

## Files to Create/Modify

1. CREATE `src/d2c/kairos.py` — heartbeat-based background agent
2. MODIFY `src/d2c/config.py` — add KAIROS feature flag

## Key Design

```python
class KairosAgent:
    """
    Paper: "A persistent background agent with tick-based heartbeats:
    when no user messages are pending, the system injects periodic <tick>
    prompts, and the model decides whether to act or sleep."

    Key design choices:
    - Terminal focus awareness: maximizes autonomous action when user is away,
      increases collaboration when present.
    - Economic throttling via SleepTool: each wake-up costs an API call;
      prompt cache expires after 5 minutes of inactivity.
    - Feature-gated: only active when KAIROS_ENABLED=true.
    """

    def __init__(self, config, loop_config, idle_timeout=30):
        self._idle_timeout = idle_timeout
        self._last_user_activity = time.monotonic()
        self._tick_count = 0
        self._sleeping = False

    async def start(self) -> AsyncGenerator:
        """Start the KAIROS heartbeat loop."""
        while True:
            await asyncio.sleep(self._idle_timeout)
            if time.monotonic() - self._last_user_activity < self._idle_timeout:
                continue  # User is active
            if self._sleeping:
                continue  # Agent chose to sleep

            tick_prompt = (f"<tick> Tick #{self._tick_count}. "
                           f"No user messages pending. You may act or sleep.</tick>")
            self._tick_count += 1
            response = await self._tick_call(tick_prompt)

            if response.action == "sleep":
                self._sleeping = True
                yield SleepEvent(duration=response.sleep_duration)
            elif response.action == "act":
                yield ActionEvent(task=response.task)

    def on_user_activity(self):
        """Called when user sends a message. Resets idle timer."""
        self._last_user_activity = time.monotonic()
        self._sleeping = False
```

## Feature Gate

```python
# Config
kairos_enabled: bool = False  # Feature-gated, off by default

# In main.py interactive:
if config.kairos_enabled:
    kairos = KairosAgent(config, loop_config)
    async for event in kairos.start():
        # Merge KAIROS actions into the main loop
        ...
```

## Edge Cases

- KAIROS disabled → zero overhead, no tick loop
- User returns during sleep → wake immediately
- Prompt cache expires → sleep is cheaper than waking
- KAIROS action conflicts with user action → user wins

## Tests (~4)

- KAIROS idle timeout triggers tick
- User activity resets idle timer
- Sleep state prevents ticks
- Feature flag off → no KAIROS behavior
