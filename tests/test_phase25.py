"""Tests for Phase 25: KAIROS Persistent Background Agent."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from d2c.kairos import (
    ActionEvent,
    KairosAgent,
    KairosResponse,
    SleepEvent,
    TickEvent,
)

# ── Tests ─────────────────────────────────────────────────────────────


class TestKairosEvents:
    def test_tick_event_fields(self):
        e = TickEvent(count=3, prompt="<tick> test</tick>")
        assert e.count == 3
        assert "test" in e.prompt

    def test_sleep_event_fields(self):
        e = SleepEvent(duration=120.0)
        assert e.duration == 120.0

    def test_action_event_fields(self):
        e = ActionEvent(task="Refactor the database module.")
        assert "Refactor" in e.task

    def test_kairos_response_defaults(self):
        r = KairosResponse()
        assert r.action == "sleep"
        assert r.sleep_duration == 300.0
        assert r.task == ""

    def test_kairos_response_act(self):
        r = KairosResponse(action="act", task="Fix security vulnerability.")
        assert r.action == "act"
        assert r.task == "Fix security vulnerability."


class TestKairosAgent:
    """Paper Section 11.6: tick-based heartbeat agent."""

    @pytest.mark.asyncio
    async def test_idle_timeout_triggers_tick(self):
        """After idle_timeout of inactivity, a TickEvent fires."""
        agent = KairosAgent(idle_timeout=0.05)
        agent._tick_call = AsyncMock(return_value=KairosResponse(action="sleep"))

        gen = agent.start()

        # First yield: TickEvent
        event = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert isinstance(event, TickEvent)
        assert event.count == 1
        assert "Tick #1" in event.prompt

        # Second yield: SleepEvent (from mock)
        event2 = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert isinstance(event2, SleepEvent)

    @pytest.mark.asyncio
    async def test_user_activity_resets_idle_timer(self):
        """Calling on_user_activity() resets the idle timer and wakes from sleep."""
        agent = KairosAgent(idle_timeout=0.3)

        # Let some time pass but not enough to trigger idle
        await asyncio.sleep(0.05)
        assert not agent.is_idle

        agent.on_user_activity()
        assert not agent.is_idle

    @pytest.mark.asyncio
    async def test_sleep_state_prevents_subsequent_ticks(self):
        """When the model chooses sleep, the agent stops generating ticks."""
        agent = KairosAgent(idle_timeout=0.05)
        agent._tick_call = AsyncMock(
            return_value=KairosResponse(action="sleep", sleep_duration=60.0)
        )

        gen = agent.start()

        # First: TickEvent
        await asyncio.wait_for(gen.__anext__(), timeout=2.0)

        # Second: SleepEvent
        sleep_event = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert isinstance(sleep_event, SleepEvent)
        assert agent.is_sleeping

        # The loop now sleeps (skips ticks). Verify no events fire while sleeping.
        # We can't wait forever, but on_user_activity wakes it.
        agent.on_user_activity()
        assert not agent.is_sleeping

    @pytest.mark.asyncio
    async def test_action_event_on_act_response(self):
        """When the model chooses to act, an ActionEvent is yielded."""
        agent = KairosAgent(idle_timeout=0.05)
        agent._tick_call = AsyncMock(
            return_value=KairosResponse(action="act", task="Run linting pass.")
        )

        gen = agent.start()

        # First: TickEvent
        await asyncio.wait_for(gen.__anext__(), timeout=2.0)

        # Second: ActionEvent
        action_event = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert isinstance(action_event, ActionEvent)
        assert "lint" in action_event.task.lower()
        assert not agent.is_sleeping  # agent should NOT be sleeping after act

    @pytest.mark.asyncio
    async def test_tick_call_error_handling(self):
        """When _tick_call raises, agent sleeps briefly and continues."""
        agent = KairosAgent(idle_timeout=0.05)
        agent._tick_call = AsyncMock(side_effect=RuntimeError("API unavailable"))

        gen = agent.start()

        # First: TickEvent
        event = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert isinstance(event, TickEvent)

        # Second: SleepEvent (error path)
        event2 = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert isinstance(event2, SleepEvent)
        assert agent.is_sleeping

    @pytest.mark.asyncio
    async def test_on_user_activity_wakes_from_sleep(self):
        """on_user_activity clears the sleeping flag."""
        agent = KairosAgent(idle_timeout=0.05)
        agent._tick_call = AsyncMock(return_value=KairosResponse(action="sleep"))

        gen = agent.start()

        await asyncio.wait_for(gen.__anext__(), timeout=2.0)  # Tick
        await asyncio.wait_for(gen.__anext__(), timeout=2.0)  # Sleep

        assert agent.is_sleeping
        agent.on_user_activity()
        assert not agent.is_sleeping

    def test_feature_flag_defaults_false(self):
        """kairos_enabled defaults to False in Config."""
        from d2c.config import Config

        config = Config.load()
        assert config.kairos_enabled is False

    @pytest.mark.asyncio
    async def test_parse_response_extract_duration(self):
        """Duration extraction from model text."""
        agent = KairosAgent(idle_timeout=1.0)

        # Minutes
        r = agent._parse_response("sleep for 10 min")
        assert r.action == "sleep"
        assert r.sleep_duration == 600.0

        # Seconds
        r = agent._parse_response("sleep 30s please")
        assert r.action == "sleep"
        assert r.sleep_duration == 30.0

        # Default
        r = agent._parse_response("sleep")
        assert r.action == "sleep"
        assert r.sleep_duration == 300.0

    @pytest.mark.asyncio
    async def test_parse_response_act(self):
        """Act response is parsed correctly."""
        agent = KairosAgent(idle_timeout=1.0)

        r = agent._parse_response("I will act: check for outdated dependencies.")
        assert r.action == "act"
        assert "outdated" in r.task

    @pytest.mark.asyncio
    async def test_parse_response_default_to_sleep(self):
        """Text with no action keywords defaults to sleep (conservative)."""
        agent = KairosAgent(idle_timeout=1.0)

        r = agent._parse_response("Some random text without clear direction.")
        assert r.action == "sleep"
