"""Live phone conversation sessions for external AI agents (VOIP-1574).

The MCP server owns the call session: the call itself, live speech-to-text
(``/transcribes``), text-to-speech (``/speakings``) and the ``/ws`` event
subscription. ``tools/phone.py`` exposes it as turn-based tools.

This module holds the pieces shared by ``events``, ``session`` and
``manager``: tunable constants, the error type, and two small async helpers.
It imports only the dependency-free ``text`` submodule.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Callable

import anyio

from voipbin_mcp.phone.text import estimate_seconds as _estimate_seconds

NIL_UUID = "00000000-0000-0000-0000-000000000000"


@dataclasses.dataclass
class PhoneConfig:
    """Every time constant of the phone feature, injectable for tests."""

    # Decisions D2 and section 4.5/4.6 of the design.
    max_call_seconds: int = 3600
    max_sessions: int = 4
    idle_hangup_seconds: float = 300.0
    unclaimed_incoming_grace_seconds: float = 15.0
    # During shutdown a ringing call is rechecked after this delay before it
    # is rejected (another MCP process may answer it); within the 5 s budget.
    shutdown_reject_delay: float = 1.0
    stt_silent_warn_seconds: float = 240.0
    ended_retention_seconds: float = 60.0
    # /ws event handling (section 4.1).
    call_event_buffer_seconds: float = 30.0
    call_event_buffer_max: int = 256
    dedupe_size: int = 4096
    ws_ready_delay: float = 0.3
    ws_backoff_initial: float = 0.5
    ws_backoff_max: float = 10.0
    events_ready_timeout: float = 10.0
    # Turn taking (section 4.2).
    barge_window_delay: float = 1.0
    listen_grace_seconds: float = 5.0
    interim_active_seconds: float = 2.0
    say_and_listen_max_block: float = 130.0
    # A listen that times out reconciles missed transcripts within this
    # budget; phone_say_and_listen reserves it inside its 130 s cap.
    reconcile_timeout: float = 3.0
    say_wait_max: float = 120.0
    # Call setup (sections 4.3, 4.4).
    poll_interval: float = 1.0
    groupcall_cleanup_interval: float = 0.5
    incoming_progress_timeout: float = 5.0
    supervisor_interval: float = 1.0
    clock: Callable[[], float] = time.monotonic
    estimate_seconds: Callable[[str], float] = _estimate_seconds


class PhoneError(Exception):
    """An operational failure reported to the agent as ``{"error", "reason"}``."""

    def __init__(self, message: str, reason: str, **extra):
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.extra = extra

    def as_dict(self) -> dict:
        data = {"error": self.message, "reason": self.reason}
        data.update(self.extra)
        return data


class Notifier:
    """Wake every waiter when shared state changes.

    Event callbacks run synchronously on the loop, so a waiter that checks its
    condition and then calls ``wait`` without an await in between cannot miss
    a notification.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def notify(self) -> None:
        event, self._event = self._event, asyncio.Event()
        event.set()

    async def wait(self, timeout: float) -> None:
        event = self._event
        if timeout <= 0:
            return
        with anyio.move_on_after(timeout):
            await event.wait()


async def wait_task_done(task: "asyncio.Task") -> None:
    """Wait for ``task`` without cancelling it when the waiter is cancelled.

    ``await task`` would propagate a cancellation of the waiter into the task,
    which for a background speaking stop would abandon the cleanup. This waits
    on a separate event instead (and never uses asyncio.shield).
    """
    if task.done():
        return
    done = asyncio.Event()
    task.add_done_callback(lambda _t: done.set())
    await done.wait()


def is_nil(value) -> bool:
    return not value or value == NIL_UUID
