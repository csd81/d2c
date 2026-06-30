"""KAIROS Persistent Background Agent. Paper Section 11.6.

A feature-gated background agent with tick-based heartbeats:
when no user messages are pending, the system injects periodic <tick>
prompts, and the model decides whether to act or sleep.

Key design:
- Terminal focus awareness: maximizes autonomous action when user is away
- Economic throttling via sleep: each wake-up costs an API call;
  prompt cache expires after 5 minutes of inactivity
- Feature-gated: only active when kairos_enabled=True
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator

logger = logging.getLogger(__name__)


# ── KAIROS events ─────────────────────────────────────────────────────

@dataclass
class TickEvent:
    """A tick was generated due to user idle timeout."""
    count: int
    prompt: str


@dataclass
class SleepEvent:
    """The model chose to sleep for a duration (seconds)."""
    duration: float = 0.0


@dataclass
class ActionEvent:
    """The model chose to perform an autonomous action."""
    task: str = ""


# ── KAIROS response ───────────────────────────────────────────────────

@dataclass
class KairosResponse:
    """Parsed decision from a tick call. The model returns either 'sleep' or 'act'."""
    action: str = "sleep"  # "sleep" | "act"
    sleep_duration: float = 300.0  # default 5 min (cache expiry window)
    task: str = ""


# ── KAIROS agent ──────────────────────────────────────────────────────

class KairosAgent:
    """Paper Section 11.6: persistent background agent with tick-based heartbeats.

    When no user messages are pending, the system injects periodic <tick>
    prompts, and the model decides whether to act or sleep.

    Terminal focus awareness: maximizes autonomous action when user is away,
    increases collaboration when present.

    Economic throttling: each wake-up costs an API call; the SleepTool
    (modelled as KairosResponse) lets the model defer activity.
    """

    def __init__(
        self,
        config: object | None = None,
        loop_config: object | None = None,
        idle_timeout: float = 30.0,
    ):
        self._idle_timeout = idle_timeout
        self._last_user_activity = time.monotonic()
        self._tick_count = 0
        self._sleeping = False
        self._config = config
        self._loop_config = loop_config

    async def start(self) -> AsyncGenerator[TickEvent | SleepEvent | ActionEvent, None]:
        """Start the KAIROS heartbeat loop, yielding events as they occur.

        This is an async generator. The caller should iterate over it in
        their event loop, merging KAIROS events with the main conversation.
        """
        while True:
            await asyncio.sleep(self._idle_timeout)

            # Skip tick if user has been active recently
            idle_duration = time.monotonic() - self._last_user_activity
            if idle_duration < self._idle_timeout:
                continue

            # Skip tick if agent chose to sleep
            if self._sleeping:
                continue

            self._tick_count += 1
            tick_prompt = (
                f"<tick> Tick #{self._tick_count}. "
                f"No user messages pending for {idle_duration:.0f}s. "
                f"You may act autonomously or sleep.</tick>"
            )

            yield TickEvent(count=self._tick_count, prompt=tick_prompt)

            try:
                response = await self._tick_call(tick_prompt)
            except Exception as e:
                logger.warning("KAIROS tick call failed: %s", e)
                # On error, sleep briefly then retry
                self._sleeping = True
                yield SleepEvent(duration=60.0)
                continue

            if response.action == "sleep":
                self._sleeping = True
                yield SleepEvent(duration=response.sleep_duration)
            elif response.action == "act":
                yield ActionEvent(task=response.task)

    async def _tick_call(self, tick_prompt: str) -> KairosResponse:
        """Send a tick prompt to the model and parse the response.

        Override this method for testing.
        """
        from d2c.loop import LoopConfig, queryLoop
        from d2c.loop import TextResponse

        if self._loop_config is None:
            return KairosResponse(action="sleep", sleep_duration=300.0)

        loop_config: LoopConfig = self._loop_config

        messages = [{"role": "user", "content": tick_prompt}]

        full_text = ""
        async for event in queryLoop(loop_config, messages):
            if isinstance(event, TextResponse):
                full_text = event.text

        return self._parse_response(full_text)

    def _parse_response(self, text: str) -> KairosResponse:
        """Parse model text response into a KairosResponse.

        Looks for action indicators in the model's output:
        - "sleep" or "SLEEP" → sleep
        - "act" or "ACT" → act with the subsequent text as task description
        """
        text_lower = text.lower()

        if "sleep" in text_lower and "act" not in text_lower:
            duration = self._extract_duration(text)
            return KairosResponse(action="sleep", sleep_duration=duration)

        if "act" in text_lower:
            return KairosResponse(action="act", task=text)

        # Default: sleep (conservative, saves API costs)
        return KairosResponse(action="sleep", sleep_duration=300.0)

    @staticmethod
    def _extract_duration(text: str) -> float:
        """Extract sleep duration from text. Defaults to 300s (5 min, cache expiry)."""
        import re
        match = re.search(r"(\d+)\s*(s|sec|second|min|minute|h|hour)", text.lower())
        if match:
            value = int(match.group(1))
            unit = match.group(2)
            if unit in ("m", "min", "minute"):
                return value * 60.0
            elif unit in ("h", "hour"):
                return value * 3600.0
            else:
                return float(value)
        return 300.0

    def on_user_activity(self) -> None:
        """Called when user sends a message. Resets idle timer and wakes from sleep."""
        self._last_user_activity = time.monotonic()
        self._sleeping = False

    @property
    def is_idle(self) -> bool:
        """True if the user has been idle longer than the timeout."""
        return (time.monotonic() - self._last_user_activity) >= self._idle_timeout

    @property
    def is_sleeping(self) -> bool:
        return self._sleeping

    @property
    def tick_count(self) -> int:
        return self._tick_count
