"""Programmatic Python API for d2c (Phase 59).

A stable, minimal wrapper around ``queryLoop()`` for IDE integrations,
automation scripts, and the local HTTP server (``d2c.server``) — anything
that wants to drive the agent loop without going through the CLI/REPL.

    from d2c.sdk import D2CClient

    client = D2CClient(cwd=".")
    async for event in client.run("summarize this repo"):
        ...

Each ``run()`` call is one turn against a persistent on-disk session
(``d2c.persistence.SessionStore``, the same mechanism the CLI uses) — a
fresh session is created on first use unless ``session_id`` is given to
resume one. Events yielded are the same ``d2c.loop`` event types the CLI
and MCP surfaces consume (``TextDelta``, ``TextResponse``,
``ToolExecutionEvent``, ``StopEvent``); nothing here changes agent-loop
behavior — this only wires up config/session/hooks around it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from d2c.config import Config
from d2c.context import assembleMessages, getSystemContext
from d2c.hooks import HookEvent
from d2c.loop import LoopEvent, queryLoop
from d2c.persistence import SessionManager, SessionStore


@dataclass
class D2CClient:
    """Small stable wrapper around the agent loop.

    ``cwd``, ``model``, and ``permission_mode`` are resolved once, at
    construction, via ``Config.load()`` (so ``.env``/trust-gate rules apply
    exactly as they do for the CLI). ``session_id`` becomes populated after
    the first ``run()`` call and is reused for subsequent turns unless
    overridden.
    """

    cwd: str | Path = "."
    model: str | None = None
    permission_mode: str | None = None
    max_turns: int = 25
    session_id: str | None = field(default=None)

    def __post_init__(self) -> None:
        self._cwd_path = Path(self.cwd)
        self._config: Config | None = None
        self._session_store: SessionStore | None = None

    def _build_config(self) -> Config:
        config = Config.load(cwd=self._cwd_path)
        if self.model:
            config.model = self.model
        if self.permission_mode:
            config.permission_mode = self.permission_mode
        config.max_turns = self.max_turns

        from d2c.trust import get_trust_gate

        if not get_trust_gate().is_project_trusted and config.permission_mode not in (
            "default",
            "plan",
        ):
            config.permission_mode = "default"
        return config

    def _resolve_session(self, config: Config, session_id: str | None) -> SessionStore:
        manager = SessionManager()
        target = session_id or self.session_id
        if target:
            store, _ = manager.resume_session(target, config.cwd)
        else:
            store = manager.create_session(config.cwd)
        self.session_id = store.session_id
        self._session_store = store
        return store

    def create_session(self) -> str:
        """Create a fresh on-disk session (without running a prompt) and
        return its id. Subsequent ``run()`` calls resume it."""
        config = self._build_config()
        self._config = config
        store = self._resolve_session(config, None)
        return store.session_id

    async def run(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
    ) -> AsyncIterator[LoopEvent]:
        """Run one turn against a session, yielding loop events as they occur.

        Creates a fresh session on first call; pass ``session_id`` (or set
        ``self.session_id``) to continue an existing one.
        """
        from d2c.main import _assemble_headless_loop_config

        config = self._build_config()
        self._config = config
        session_store = self._resolve_session(config, session_id)

        loop_config, hook_registry, _usage_tracker = await _assemble_headless_loop_config(
            config, session_store
        )

        system_context = getSystemContext(config)
        full_prompt, messages = assembleMessages(
            loop_config.system_prompt,
            system_context,
            loop_config.user_context,
            [{"role": "user", "content": prompt}],
        )
        loop_config.system_prompt = full_prompt

        await hook_registry.fire(
            HookEvent.SETUP,
            {"session_id": session_store.session_id, "model": config.model},
        )
        ups = await hook_registry.fire(
            HookEvent.USER_PROMPT_SUBMIT,
            {"prompt": prompt, "session_id": session_store.session_id},
        )
        if getattr(ups, "decision", None) == "deny" or getattr(ups, "veto", False):
            await hook_registry.fire(
                HookEvent.SESSION_END, {"session_id": session_store.session_id}
            )
            return
        if getattr(ups, "additional_context", None):
            messages.insert(0, {"role": "user", "content": ups.additional_context})

        try:
            async for event in queryLoop(loop_config, messages):
                yield event
        finally:
            from d2c.observability import audit
            from d2c.usage import audit_session_usage

            audit_session_usage(_usage_tracker.session, session_id=session_store.session_id)
            audit("session_end", session_id=session_store.session_id)
            await hook_registry.fire(
                HookEvent.SESSION_END, {"session_id": session_store.session_id}
            )
