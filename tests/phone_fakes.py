"""Shared fakes for the phone tests (imported by test_phone_session/test_tools_phone).

Everything here is offline: HTTP goes to respx, events are injected straight
into the SessionManager instead of a websocket.
"""

from __future__ import annotations

import asyncio
import json

import httpx

import voipbin_mcp.server
from voipbin_mcp.phone import PhoneConfig
from voipbin_mcp.phone.events import CallEvent, InterimEvent, TranscriptEvent
from voipbin_mcp.phone.manager import SessionManager, reset_manager

BASE = "https://api.voipbin.net/v1.0"


class FakeHub:
    """Stands in for EventHub: always connected, events are injected by tests."""

    def __init__(self, url, headers, customer_id, on_event, on_reconnect, config):
        self.url = url
        self.headers = headers
        self.customer_id = customer_id
        self.on_event = on_event
        self.on_reconnect = on_reconnect
        self.connected = True
        self.runs = 0

    async def wait_ready(self, timeout):
        return self.connected

    async def run(self):
        self.runs += 1
        await asyncio.Event().wait()


class ManualClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def fast_config(**overrides) -> PhoneConfig:
    values = dict(
        poll_interval=0.05,
        groupcall_cleanup_interval=0.02,
        incoming_progress_timeout=0.5,
        supervisor_interval=3600.0,
        barge_window_delay=0.05,
        listen_grace_seconds=0.3,
        interim_active_seconds=0.2,
        unclaimed_incoming_grace_seconds=0.15,
        events_ready_timeout=0.5,
        reconcile_timeout=0.5,
        estimate_seconds=lambda text: 0.2,
    )
    values.update(overrides)
    return PhoneConfig(**values)


def make_manager(exits: list | None = None, **overrides) -> SessionManager:
    manager = SessionManager(
        voipbin_mcp.server.get_client,
        fast_config(**overrides),
        hub_factory=FakeHub,
        install_signals=False,
        exit_func=(exits.append if exits is not None else (lambda code: None)),
    )
    reset_manager(manager)
    return manager


async def teardown_manager(manager: SessionManager) -> None:
    """Cancel every background task before respx is torn down."""
    tasks = set(manager._tasks)
    for session in list(manager.sessions.values()) + list(manager.pending):
        tasks |= set(session._tasks)
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except BaseException:
            pass
    reset_manager(None)
    # Every slot release must pair with a reserve (release_slot counts extras).
    assert manager.slot_underflows == 0, "phone session slot released more often than reserved"
    assert manager.slots_in_use >= 0


async def settle(manager: SessionManager, rounds: int = 3) -> None:
    """Let spawned background tasks (speaking stops, rejections) finish."""
    for _ in range(rounds):
        tasks = set()
        for session in list(manager.sessions.values()):
            tasks |= set(session._tasks)
        tasks |= {t for t in manager._tasks if t not in (manager._hub_task, manager._supervisor_task)}
        pending = [t for t in tasks if not t.done()]
        if not pending:
            await asyncio.sleep(0)
            continue
        await asyncio.wait(pending, timeout=2)


def ok(payload=None, status=200):
    return httpx.Response(status, json=payload if payload is not None else {})


def yielding(response=None, delay=0.01, entered: asyncio.Event | None = None):
    """An async respx side effect that awaits before answering.

    A synchronous mock answers without ever reaching an await, so a request
    made under a cancelled (unshielded) scope still "succeeds" and a
    cancellation test cannot tell a shielded cleanup from an unshielded one.
    This one yields to the loop first: an unshielded request is cancelled
    there and, because respx records a call only once it has a response, it
    then does not show up in ``route.call_count``.

    ``response`` is an httpx.Response, a callable ``request -> Response``, or
    None for an empty 200. ``entered`` is set when the request arrives.
    """

    async def handler(request):
        if entered is not None:
            entered.set()
        await asyncio.sleep(delay)
        if callable(response):
            return response(request)
        return response if response is not None else ok()

    return handler


def yielding_ok(payload=None, delay=0.01, entered: asyncio.Event | None = None):
    return yielding(lambda request: ok(payload), delay, entered)


def err(status, message="boom", reason="ERR"):
    return httpx.Response(status, json={"error": {"message": message, "reason": reason, "status": status}})


def body(call) -> dict:
    content = call.request.content
    return json.loads(content) if content else {}


def call_event(call_id, status, direction="outgoing", **kw) -> CallEvent:
    return CallEvent(id=call_id, status=status, direction=direction, **kw)


def transcript(tid, message, transcribe_id="tr1") -> TranscriptEvent:
    return TranscriptEvent(id=tid, transcribe_id=transcribe_id, direction="in", message=message)


_interim_seq = [0]


def interim(message, transcribe_id="tr1") -> InterimEvent:
    _interim_seq[0] += 1
    return InterimEvent(
        id=f"interim-{_interim_seq[0]}", transcribe_id=transcribe_id, direction="in", message=message
    )


def answered_session(manager: SessionManager, call_id="c1", transcribe_id="tr1", speaking_id="sp1", **kw):
    """A session as it is right after start_media, registered for routing."""
    session = manager.new_session(direction="outgoing", **kw)
    session.call_id = call_id
    session.state = "dialing"
    manager.sessions[call_id] = session
    session.mark_answered()
    session.transcribe_id = transcribe_id
    manager._by_transcribe[transcribe_id] = session
    session.speaking_id = speaking_id
    session.last_stt_event_at = session.clock()
    return session
