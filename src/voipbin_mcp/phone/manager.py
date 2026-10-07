"""SessionManager: every live phone session of this MCP process.

Owns the session map, the concurrent-session limit, incoming-call waiters,
the unclaimed-incoming rejection timers, the supervisor (idle watchdog and
maximum-duration deadline) and the shutdown cleanup.

Cancellation rules (design 4.7). FastMCP cancels a tool handler through an
anyio cancel scope, which re-delivers the cancellation at every await while
the scope stays cancelled. So:

- the POST /calls request and the registration of its result run inside
  ``anyio.CancelScope(shield=True)``;
- every cleanup path (hangup, speaking stop, groupcall hangup, shutdown) runs
  inside ``anyio.move_on_after(5, shield=True)`` and only logs failures;
- asyncio.shield is never used.

The shield sits at the cleanup entry point (``_cleanup_outgoing``,
``_reject_if_ringing``, ``shutdown``, the configure rollback); the helpers they
call (``_hangup_groupcall_now``, ``_reject_now``, ``PhoneSession._hangup_now``)
are unshielded, so every shield is the only guard of its path.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import json
import logging
import os
import signal
from typing import Callable

import anyio

from voipbin_mcp.client import VoIPbinAPIError
from voipbin_mcp.phone import NIL_UUID, Notifier, PhoneConfig, PhoneError, is_nil
from voipbin_mcp.phone.events import (
    CallEvent,
    Event,
    EventHub,
    InterimEvent,
    TranscriptEvent,
    build_ws_url,
)
from voipbin_mcp.phone.session import PhoneSession, api_post, is_not_found

logger = logging.getLogger(__name__)

MCP_FLOW_MARKER = "incoming"
INCOMING_FLOW_SLEEP_MS = 3600000


def incoming_flow_name(number_id: str) -> str:
    return f"voipbin-mcp incoming {number_id}"


def parse_marker(flow: dict) -> dict | None:
    """The MCP marker stored in a flow's detail, or None for a foreign flow."""
    detail = flow.get("detail") if isinstance(flow, dict) else None
    if not isinstance(detail, str) or not detail:
        return None
    try:
        data = json.loads(detail)
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("voipbin_mcp") == MCP_FLOW_MARKER:
        return data
    return None


@dataclasses.dataclass
class IncomingWaiter:
    number_id: str
    flow_id: str
    candidates: collections.deque = dataclasses.field(default_factory=collections.deque)
    buffered: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class UnclaimedCall:
    event: CallEvent
    task: asyncio.Task | None = None


class _CallerGone(Exception):
    """The claimed incoming call was already hung up by the caller."""


class SessionManager:
    def __init__(
        self,
        client_factory: Callable,
        config: PhoneConfig | None = None,
        *,
        hub_factory: Callable | None = None,
        install_signals: bool = True,
        exit_func: Callable[[int], None] = os._exit,
    ):
        self._client_factory = client_factory
        self._client = None
        self.config = config or PhoneConfig()
        self.clock = self.config.clock
        self._hub_factory = hub_factory or self._default_hub
        self._install_signals = install_signals
        self._exit = exit_func

        self.hub = None
        self._hub_task: asyncio.Task | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()
        self._signals_installed = False
        self._signal_task: asyncio.Task | None = None
        self._closing = False
        self.customer_id = ""

        self.sessions: dict[str, PhoneSession] = {}
        self.pending: set[PhoneSession] = set()
        self._by_transcribe: dict[str, PhoneSession] = {}
        self.waiters: dict[str, IncomingWaiter] = {}
        self.incoming_flow_ids: set[str] = set()
        self.unclaimed: dict[str, UnclaimedCall] = {}
        self._handled: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._call_buffer: collections.deque = collections.deque()
        self._seq = 0
        self.changed = Notifier()
        self.slots_in_use = 0
        self.inflight_posts = 0
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------- plumbing

    @property
    def client(self):
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _default_hub(self, url, headers, customer_id, on_event, on_reconnect, config):
        return EventHub(url, headers, customer_id, on_event, on_reconnect, config)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def events_connected(self) -> bool:
        return bool(
            self.hub is not None
            and getattr(self.hub, "connected", False)
            and self._hub_task is not None
            and not self._hub_task.done()
        )

    def has_state(self) -> bool:
        return bool(
            self.hub is not None
            or self.sessions
            or self.pending
            or self.unclaimed
            or self.waiters
            or self.inflight_posts
        )

    def _mark_handled(self, call_id: str) -> None:
        self._handled[call_id] = None
        while len(self._handled) > self.config.dedupe_size:
            self._handled.popitem(last=False)

    def reserve_slot(self) -> None:
        """Synchronously take a session slot before any request goes out."""
        if self.slots_in_use >= self.config.max_sessions:
            raise PhoneError(
                f"the limit of {self.config.max_sessions} concurrent phone sessions "
                "is reached; hang up a call first",
                "session_limit",
            )
        self.slots_in_use += 1

    def release_slot(self) -> None:
        self.slots_in_use = max(0, self.slots_in_use - 1)

    def new_session(self, *, direction: str, **kwargs) -> PhoneSession:
        session = PhoneSession(
            self.client,
            direction=direction,
            config=self.config,
            events_connected=self.events_connected,
            on_transcribe=self._register_transcribe,
            on_end=self._session_ended,
            **kwargs,
        )
        return session

    def _register_transcribe(self, session: PhoneSession, transcribe_id: str) -> None:
        self._by_transcribe[transcribe_id] = session

    def _session_ended(self, session: PhoneSession) -> None:
        if session.slot_held:
            session.slot_held = False
            self.release_slot()
        self.pending.discard(session)
        self.changed.notify()

    def get_session(self, call_id: str) -> PhoneSession:
        session = self.sessions.get(call_id)
        if session is None:
            raise PhoneError(
                f"no phone session for call_id {call_id!r} in this MCP process",
                "unknown_call",
            )
        return session

    # -------------------------------------------------------------- startup

    async def ensure_started(self) -> None:
        """Lazily open the event stream (once) and wait until it is ready."""
        async with self._start_lock:
            if self.hub is None:
                client = self.client
                customer = await client.get("/customer")
                self.customer_id = str(customer.get("id") or "")
                if not self.customer_id:
                    raise PhoneError("GET /customer returned no id", "events_unavailable")
                url, headers = build_ws_url(client.base_url, client.api_key, client.auth_transport)
                self.hub = self._hub_factory(
                    url, headers, self.customer_id, self.on_event, self._on_reconnect, self.config
                )
                self._hub_task = self._spawn(self.hub.run())
                self._supervisor_task = self._spawn(self._supervise())
                if self._install_signals:
                    self.install_signal_handlers()
        if not await self.hub.wait_ready(self.config.events_ready_timeout):
            raise PhoneError(
                "the /ws event stream is not connected; try again shortly",
                "events_unavailable",
            )

    def install_signal_handlers(self) -> None:
        if self._signals_installed:
            return
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, self._on_signal, signum)
            except (NotImplementedError, RuntimeError, ValueError):  # pragma: no cover
                logger.debug("cannot install a handler for signal %s", signum)
        self._signals_installed = True

    def _on_signal(self, signum: int) -> None:
        if self._signal_task is not None:
            return  # a second signal during cleanup is ignored
        self._signal_task = self._spawn(self._exit_after_cleanup(signum))

    async def _exit_after_cleanup(self, signum: int) -> None:
        try:
            await self.shutdown()
        finally:
            self._exit(128 + int(signum))

    # --------------------------------------------------------------- events

    def on_event(self, event: Event) -> None:
        if isinstance(event, CallEvent):
            self._on_call_event(event)
        elif isinstance(event, TranscriptEvent):
            session = self._by_transcribe.get(event.transcribe_id)
            if session is not None:
                session.on_transcript(event)
        elif isinstance(event, InterimEvent):
            session = self._by_transcribe.get(event.transcribe_id)
            if session is not None:
                session.on_interim(event)

    def _on_call_event(self, event: CallEvent) -> None:
        now = self.clock()
        self._seq += 1
        self._call_buffer.append((self._seq, now, event))
        horizon = now - self.config.call_event_buffer_seconds
        while self._call_buffer and (
            len(self._call_buffer) > self.config.call_event_buffer_max
            or self._call_buffer[0][1] < horizon
        ):
            self._call_buffer.popleft()

        session = self.sessions.get(event.id)
        if session is not None:
            session.on_call_event(event)
        if event.direction == "incoming":
            self._route_incoming(event)
        self.changed.notify()

    def _route_incoming(self, event: CallEvent) -> None:
        if event.status == "ringing":
            if (
                event.flow_id not in self.incoming_flow_ids
                or event.id in self._handled
                or event.id in self.sessions
                or event.id in self.unclaimed
            ):
                return
            for waiter in self.waiters.values():
                if waiter.flow_id == event.flow_id:
                    if all(c.id != event.id for c in waiter.candidates):
                        waiter.candidates.append(event)
                    return
            self._start_unclaimed(event)
        elif event.status in ("progressing", "hangup"):
            # Answered or abandoned elsewhere: never reject it.
            entry = self.unclaimed.pop(event.id, None)
            if entry is not None and entry.task is not None:
                entry.task.cancel()
            for waiter in self.waiters.values():
                for candidate in list(waiter.candidates):
                    if candidate.id == event.id:
                        waiter.candidates.remove(candidate)

    def _start_unclaimed(self, event: CallEvent) -> None:
        entry = UnclaimedCall(event)
        self.unclaimed[event.id] = entry
        entry.task = self._spawn(self._reject_later(entry))

    async def _reject_later(self, entry: UnclaimedCall) -> None:
        await anyio.sleep(self.config.unclaimed_incoming_grace_seconds)
        if self.unclaimed.get(entry.event.id) is not entry:
            return
        # Remove before any await so a new waiter cannot claim a call that is
        # being rejected.
        del self.unclaimed[entry.event.id]
        self._mark_handled(entry.event.id)
        await self._reject_if_ringing(entry.event.id)

    async def _reject_now(self, call_id: str) -> None:
        """Hang up a call still ringing (unshielded; callers provide the shield)."""
        try:
            call = await self.client.get(f"/calls/{call_id}")
            if call.get("status") == "ringing":
                await api_post(self.client, f"/calls/{call_id}/hangup")
        except Exception as exc:
            logger.info("rejecting unclaimed call %s: %s", call_id, exc)

    async def _reject_if_ringing(self, call_id: str) -> None:
        with anyio.move_on_after(5, shield=True):
            await self._reject_now(call_id)

    def _on_reconnect(self) -> None:
        for session in list(self.sessions.values()):
            if not session.ended:
                self._spawn(session.reconcile())

    def _replay_buffer(self, session: PhoneSession) -> None:
        for _seq, _ts, event in list(self._call_buffer):
            if event.id == session.call_id:
                session.on_call_event(event)

    # ----------------------------------------------------------- supervisor

    async def _supervise(self) -> None:
        while True:
            await asyncio.sleep(self.config.supervisor_interval)
            try:
                self.supervise_once()
            except Exception:  # pragma: no cover - the watchdog must keep running
                logger.exception("phone supervisor pass failed")

    def supervise_once(self) -> None:
        """One watchdog pass; independent of the EventHub's health."""
        now = self.clock()
        for call_id, session in list(self.sessions.items()):
            if session.ended:
                ended_at = session.ended_at if session.ended_at is not None else now
                if now - ended_at >= self.config.ended_retention_seconds:
                    del self.sessions[call_id]
                    if self._by_transcribe.get(session.transcribe_id) is session:
                        del self._by_transcribe[session.transcribe_id]
                continue
            if session.state != "answered" or session.hangup_requested:
                continue
            if session.active_ops > 0:
                session.last_tool_at = now  # a blocking listen is activity
            if session.deadline is not None and now >= session.deadline:
                session.hangup_requested = True
                self._spawn(session.hangup("max_duration"))
            elif now - session.last_tool_at >= self.config.idle_hangup_seconds:
                session.hangup_requested = True
                self._spawn(session.hangup("idle_timeout"))
        if self.hub is not None and self._hub_task is not None and self._hub_task.done() and not self._closing:
            if not self._hub_task.cancelled() and self._hub_task.exception() is not None:
                logger.warning("phone event hub stopped: %r; restarting", self._hub_task.exception())
            self._hub_task = self._spawn(self.hub.run())

    # ------------------------------------------------------------- outgoing

    async def call_start(
        self,
        *,
        source_number: str,
        destination_type: str,
        destination_target: str,
        language: str,
        voice_id: str,
        max_duration_seconds: int,
        answer_timeout_seconds: int,
    ) -> dict:
        self._refuse_if_closing()
        await self.ensure_started()
        self._refuse_if_closing()
        self.reserve_slot()
        session = self.new_session(
            direction="outgoing",
            language=language,
            voice_id=voice_id,
            max_duration_seconds=max_duration_seconds,
        )
        session.slot_held = True

        if destination_type == "extension":
            destination = {"type": "extension", "target_name": destination_target}
        else:
            destination = {"type": destination_type, "target": destination_target}
        body = {
            "source": {"type": "tel", "target": source_number},
            "destinations": [destination],
            "actions": [{"type": "sleep", "option": {"duration": max_duration_seconds * 1000}}],
        }
        candidates: list[str] = []
        try:
            # A POST that succeeded on the server must always be registered,
            # or a cancellation here would leave a ringing call nobody owns.
            with anyio.CancelScope(shield=True):
                self.inflight_posts += 1
                try:
                    response = await api_post(self.client, "/calls", body)
                    calls = response.get("calls") or []
                    groupcalls = response.get("groupcalls") or []
                    if calls and calls[0].get("id"):
                        session.call_id = str(calls[0]["id"])
                        session.state = "dialing"
                        self.sessions[session.call_id] = session
                        self._replay_buffer(session)
                    elif groupcalls and groupcalls[0].get("id"):
                        session.groupcall_id = str(groupcalls[0]["id"])
                        session.state = "dialing"
                        self.pending.add(session)
                        candidates = [str(c) for c in groupcalls[0].get("call_ids") or []]
                        session.candidate_call_ids = candidates
                    else:
                        raise PhoneError(
                            "POST /calls returned neither a call nor a groupcall",
                            "call_failed",
                        )
                finally:
                    self.inflight_posts -= 1
                    self.changed.notify()

            if session.call_id:
                await self._await_single_answer(session, answer_timeout_seconds)
            else:
                await self._resolve_groupcall(session, candidates, answer_timeout_seconds)
            await session.start_media()
        except BaseException as exc:
            reason = exc.reason if isinstance(exc, PhoneError) else "setup_failed"
            await self._cleanup_outgoing(session, candidates, reason)
            raise
        # The idle watchdog counts from the answer, not from the dial.
        session.touch()
        return {
            "call_id": session.call_id,
            "status": "answered",
            "max_duration_seconds": max_duration_seconds,
            "hint": (
                "Use phone_say_and_listen(call_id, text) for each turn, "
                "phone_listen to keep listening, and phone_hangup when done."
            ),
            "events_connected": self.events_connected(),
        }

    async def _await_single_answer(self, session: PhoneSession, timeout: float) -> None:
        end = self.clock() + timeout
        next_poll = self.clock() + self.config.poll_interval
        while True:
            if session.state == "answered":
                return
            if session.ended:
                raise PhoneError(
                    "the call ended before it was answered",
                    "call_failed",
                    hangup_reason=session.ended_reason,
                )
            now = self.clock()
            if now >= end:
                raise PhoneError(f"no answer within {timeout} seconds", "no_answer")
            if now >= next_poll:
                next_poll = now + self.config.poll_interval
                try:
                    call = await self.client.get(f"/calls/{session.call_id}")
                    status = call.get("status")
                    if status == "progressing":
                        session.mark_answered()
                    elif status == "hangup":
                        session.mark_ended(call.get("hangup_reason") or "hangup", call.get("hangup_by") or "")
                except VoIPbinAPIError as exc:
                    if is_not_found(exc):
                        session.mark_ended("not_found")
                continue
            await session.changed.wait(min(end, next_poll) - now)

    def _confirm_leg(self, session: PhoneSession, call_id: str) -> None:
        session.call_id = call_id
        self.pending.discard(session)
        self.sessions[call_id] = session
        session.mark_answered()

    async def _resolve_groupcall(self, session: PhoneSession, candidates: list[str], timeout: float) -> None:
        gid = session.groupcall_id
        cands: list[str] = list(candidates)
        if not cands:
            group = await self.client.get(f"/groupcalls/{gid}")
            cands = [str(c) for c in group.get("call_ids") or []]
        ended: set[str] = set()
        cursor = 0
        end = self.clock() + timeout
        next_poll = self.clock() + self.config.poll_interval
        while True:
            for seq, _ts, event in list(self._call_buffer):
                if seq <= cursor:
                    continue
                cursor = seq
                if event.id not in cands and not (event.groupcall_id and event.groupcall_id == gid):
                    continue
                if event.id not in cands:
                    cands.append(event.id)
                if event.status == "progressing":
                    self._confirm_leg(session, event.id)
                    return
                if event.status == "hangup":
                    ended.add(event.id)
            if cands and all(c in ended for c in cands):
                raise PhoneError("every leg of the call ended unanswered", "call_failed")
            now = self.clock()
            if now >= end:
                raise PhoneError(f"no answer within {timeout} seconds", "no_answer")
            if now >= next_poll:
                next_poll = now + self.config.poll_interval
                try:
                    group = await self.client.get(f"/groupcalls/{gid}")
                except VoIPbinAPIError as exc:
                    if is_not_found(exc):
                        raise PhoneError("the groupcall no longer exists", "call_failed") from exc
                    group = {}
                answer = group.get("answer_call_id")
                if not is_nil(answer):
                    self._confirm_leg(session, str(answer))
                    return
                if group.get("status") == "hangup":
                    raise PhoneError("the call ended unanswered", "call_failed")
                for c in group.get("call_ids") or []:
                    if str(c) not in cands:
                        cands.append(str(c))
                for c in cands:
                    if c in ended:
                        continue
                    try:
                        call = await self.client.get(f"/calls/{c}")
                    except VoIPbinAPIError as exc:
                        if is_not_found(exc):
                            ended.add(c)
                        continue
                    if call.get("status") == "progressing":
                        self._confirm_leg(session, c)
                        return
                    if call.get("status") == "hangup":
                        ended.add(c)
                continue
            await self.changed.wait(min(end, next_poll) - now)

    async def _cleanup_outgoing(self, session: PhoneSession, candidates: list[str], reason: str) -> None:
        with anyio.move_on_after(5, shield=True):
            if session.call_id:
                await session._hangup_now(reason)
            else:
                session.mark_ended(reason)
                if session.groupcall_id:
                    await self._hangup_groupcall_now(session.groupcall_id, candidates)
        self.pending.discard(session)

    async def _hangup_groupcall_now(self, groupcall_id: str, known: list[str]) -> None:
        """Hang up a groupcall and every leg it created (legs start async).

        Unshielded and unbounded by itself: callers run it inside
        ``anyio.move_on_after(5, shield=True)``.
        """
        try:
            await api_post(self.client, f"/groupcalls/{groupcall_id}/hangup")
        except Exception as exc:
            logger.info("groupcall %s hangup: %s", groupcall_id, exc)
        legs: list[str] = list(known)
        while True:
            group: dict | None = None
            try:
                group = await self.client.get(f"/groupcalls/{groupcall_id}")
            except VoIPbinAPIError as exc:
                if is_not_found(exc):
                    group = None
                    if not legs:
                        break
            except Exception as exc:  # pragma: no cover
                logger.info("groupcall %s lookup: %s", groupcall_id, exc)
            for c in (group or {}).get("call_ids") or []:
                if str(c) not in legs:
                    legs.append(str(c))
            live = 0
            for c in legs:
                try:
                    call = await self.client.get(f"/calls/{c}")
                except VoIPbinAPIError as exc:
                    if not is_not_found(exc):
                        live += 1
                    continue
                except Exception:  # pragma: no cover
                    live += 1
                    continue
                if call.get("status") != "hangup":
                    live += 1
                    try:
                        await api_post(self.client, f"/calls/{c}/hangup")
                    except Exception as exc:
                        logger.info("leg %s hangup: %s", c, exc)
            if legs and live == 0:
                break
            await anyio.sleep(self.config.groupcall_cleanup_interval)

    # ------------------------------------------------------------- incoming

    async def _marker_flow(self, flow_id: str) -> dict | None:
        if is_nil(flow_id):
            return None
        try:
            flow = await self.client.get(f"/flows/{flow_id}")
        except VoIPbinAPIError as exc:
            if is_not_found(exc):
                return None
            raise
        return parse_marker(flow)

    async def incoming_configure(
        self, number_id: str, enabled: bool, restore_call_flow_id: str = "", clear: bool = False
    ) -> dict:
        client = self.client
        if not enabled:
            if number_id in self.waiters:
                raise PhoneError(
                    "this process is waiting for calls on the number; let "
                    "phone_wait_incoming finish first",
                    "number_busy",
                )
            for session in self.sessions.values():
                if session.number_id == number_id and not session.ended:
                    raise PhoneError(
                        "an incoming call on this number is still active; hang it up first",
                        "number_busy",
                    )

        number = await client.get(f"/numbers/{number_id}")
        current = str(number.get("call_flow_id") or "")
        marker = await self._marker_flow(current)

        if enabled:
            if marker is not None:
                return {
                    "number": number.get("number"),
                    "call_flow_id": current,
                    "previous_call_flow_id": marker.get("previous_call_flow_id"),
                    "enabled": True,
                }
            previous = None if is_nil(current) else current
            detail = json.dumps(
                {"voipbin_mcp": MCP_FLOW_MARKER, "number_id": number_id, "previous_call_flow_id": previous}
            )
            flow = await api_post(
                client,
                "/flows",
                {
                    "name": incoming_flow_name(number_id),
                    "detail": detail,
                    "actions": [{"type": "sleep", "option": {"duration": INCOMING_FLOW_SLEEP_MS}}],
                },
            )
            flow_id = str(flow.get("id") or "")
            try:
                await client.put(f"/numbers/{number_id}/flow_ids", json={"call_flow_id": flow_id})
            except BaseException:
                with anyio.move_on_after(5, shield=True):
                    try:
                        await client.delete(f"/flows/{flow_id}")
                    except Exception as exc:
                        logger.info("removing flow %s after a failed PUT: %s", flow_id, exc)
                raise
            return {
                "number": number.get("number"),
                "call_flow_id": flow_id,
                "previous_call_flow_id": previous,
                "enabled": True,
            }

        if marker is None:
            raise PhoneError(
                "the number's call flow is not an MCP incoming flow (already disabled?)",
                "not_configured",
            )
        target = restore_call_flow_id or marker.get("previous_call_flow_id") or ""
        if is_nil(target):
            if not clear:
                raise PhoneError(
                    "no previous call flow is stored for this number; pass "
                    "restore_call_flow_id, or clear=true to leave the number without a call flow",
                    "no_previous_flow",
                )
            target = NIL_UUID
        else:
            # number-manager does not check that the flow exists.
            try:
                await client.get(f"/flows/{target}")
            except VoIPbinAPIError as exc:
                if is_not_found(exc):
                    raise PhoneError(
                        f"the flow to restore ({target}) does not exist",
                        "restore_flow_missing",
                    ) from exc
                raise
        await client.put(f"/numbers/{number_id}/flow_ids", json={"call_flow_id": target})
        try:
            await client.delete(f"/flows/{current}")
        except VoIPbinAPIError as exc:
            logger.info("deleting MCP flow %s: %s", current, exc)
        return {
            "number": number.get("number"),
            "call_flow_id": target,
            "previous_call_flow_id": current,
            "enabled": False,
        }

    def _refuse_if_closing(self) -> None:
        if self._closing:
            raise PhoneError("the MCP server is shutting down", "shutting_down")

    async def _next_incoming(self, waiter: IncomingWaiter, deadline: float) -> CallEvent | None:
        while True:
            # Shutdown hangs up what exists; it must not race a new answer.
            self._refuse_if_closing()
            # 1. a ringing call still inside the rejection grace period
            for call_id, entry in list(self.unclaimed.items()):
                if entry.event.flow_id == waiter.flow_id:
                    del self.unclaimed[call_id]
                    if entry.task is not None:
                        entry.task.cancel()
                    return entry.event
            # 2. a call that arrived just before this waiter started. It is
            # removed only after the check, so a cancellation during the GET
            # leaves it for the unclaimed handover in wait_incoming's finally.
            while waiter.buffered:
                event = waiter.buffered[0]
                if event.id in self._handled or event.id in self.sessions:
                    waiter.buffered.pop(0)
                    continue
                try:
                    call = await self.client.get(f"/calls/{event.id}")
                except VoIPbinAPIError:
                    waiter.buffered.pop(0)
                    continue
                waiter.buffered.pop(0)
                if call.get("status") == "ringing":
                    self._refuse_if_closing()
                    return event
                self._mark_handled(event.id)
            # 3. live events
            while waiter.candidates:
                event = waiter.candidates.popleft()
                if event.id not in self._handled and event.id not in self.sessions:
                    return event
            now = self.clock()
            if now >= deadline:
                return None
            await self.changed.wait(deadline - now)

    async def wait_incoming(
        self,
        *,
        number_id: str,
        timeout_seconds: float,
        greeting: str,
        greeting_language: str,
        language: str,
        voice_id: str,
        max_duration_seconds: int,
    ) -> dict:
        self._refuse_if_closing()
        await self.ensure_started()
        number = await self.client.get(f"/numbers/{number_id}")
        flow_id = str(number.get("call_flow_id") or "")
        if await self._marker_flow(flow_id) is None:
            raise PhoneError(
                "the number is not set up for incoming calls; call "
                "phone_incoming_configure(number_id, enabled=true) first",
                "not_configured",
            )
        if number_id in self.waiters:
            raise PhoneError("this process is already waiting for calls on the number", "waiter_exists")
        self._refuse_if_closing()
        self.reserve_slot()
        slot_owned = True
        waiter = IncomingWaiter(number_id, flow_id)
        handled_or_live = set(self._handled) | set(self.sessions) | set(self.unclaimed)
        waiter.buffered = [
            event
            for _seq, _ts, event in self._call_buffer
            if event.direction == "incoming"
            and event.status == "ringing"
            and event.flow_id == flow_id
            and event.id not in handled_or_live
        ]
        self.waiters[number_id] = waiter
        self.incoming_flow_ids.add(flow_id)
        deadline = self.clock() + timeout_seconds
        try:
            while True:
                event = await self._next_incoming(waiter, deadline)
                if event is None:
                    return {"status": "timed_out", "timed_out": True, "events_connected": self.events_connected()}
                self._mark_handled(event.id)
                session = self.new_session(
                    direction="incoming",
                    language=language,
                    voice_id=voice_id,
                    max_duration_seconds=max_duration_seconds,
                    number_id=number_id,
                )
                session.call_id = event.id
                session.state = "ringing"
                session.caller = event.source
                self.sessions[event.id] = session
                session.slot_held = True
                slot_owned = False
                try:
                    await self._answer_incoming(session, greeting, greeting_language)
                except _CallerGone:
                    # The caller hung up before we answered: keep waiting.
                    session.slot_held = False
                    slot_owned = True
                    session.mark_ended("caller_hangup")
                    continue
                except BaseException:
                    await session.hangup("setup_failed")
                    raise
                # The idle watchdog counts from the answer, not from the wait.
                session.touch()
                return {
                    "call_id": session.call_id,
                    "status": "answered",
                    "caller": session.caller,
                    "max_duration_seconds": max_duration_seconds,
                    "events_connected": self.events_connected(),
                }
        finally:
            if self.waiters.get(number_id) is waiter:
                del self.waiters[number_id]
            # Ringing calls this waiter saw but did not take (live candidates,
            # and pre-wait buffered ones left by a cancellation or by taking
            # another call first) fall back to the unclaimed rejection path.
            leftovers = list(waiter.buffered) + list(waiter.candidates)
            waiter.buffered.clear()
            waiter.candidates.clear()
            for event in leftovers:
                if (
                    event.id not in self._handled
                    and event.id not in self.sessions
                    and event.id not in self.unclaimed
                    and not self._closing
                ):
                    self._start_unclaimed(event)
            if slot_owned:
                self.release_slot()

    async def _answer_incoming(self, session: PhoneSession, greeting: str, greeting_language: str) -> None:
        try:
            # /talk answers the ringing call, then plays the greeting.
            await api_post(
                self.client,
                f"/calls/{session.call_id}/talk",
                {"text": greeting, "language": greeting_language},
            )
        except VoIPbinAPIError:
            gone = False
            try:
                call = await self.client.get(f"/calls/{session.call_id}")
                gone = call.get("status") == "hangup"
            except VoIPbinAPIError as exc:
                gone = is_not_found(exc)
            if gone:
                raise _CallerGone()
            raise
        session.greeting_until = self.clock() + self.config.estimate_seconds(greeting)
        await self._await_single_answer(session, self.config.incoming_progress_timeout)
        # STT and TTS only work when created after the answer.
        await session.start_media()

    # ------------------------------------------------------------- shutdown

    async def shutdown(self) -> None:
        """Hang up everything this process owns (5 second budget, shielded)."""
        # Set first: no new call may start or be answered from here on.
        self._closing = True
        self.changed.notify()
        if not self.has_state():
            return
        with anyio.move_on_after(5, shield=True):
            while self.inflight_posts > 0:
                await self.changed.wait(0.1)
            unclaimed = list(self.unclaimed.items())
            self.unclaimed.clear()
            for _call_id, entry in unclaimed:
                if entry.task is not None:
                    entry.task.cancel()
            async with anyio.create_task_group() as tg:
                for session in list(self.sessions.values()):
                    if not session.ended:
                        tg.start_soon(session._hangup_now, "shutdown")
                for session in list(self.pending):
                    if not session.call_id and session.groupcall_id and not session.ended:
                        session.mark_ended("shutdown")
                        tg.start_soon(
                            self._hangup_groupcall_now, session.groupcall_id, list(session.candidate_call_ids)
                        )
                for call_id, _entry in unclaimed:
                    tg.start_soon(self._reject_now, call_id)
        for task in (self._hub_task, self._supervisor_task):
            if task is not None and not task.done():
                task.cancel()


_manager: SessionManager | None = None


def get_manager(client_factory: Callable) -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager(client_factory)
    return _manager


def current_manager() -> SessionManager | None:
    return _manager


def reset_manager(manager: SessionManager | None = None) -> None:
    """Replace the process-wide manager (tests)."""
    global _manager
    _manager = manager


async def shutdown_if_started() -> None:
    """Server exit hook: a no-op (no client, no network) without phone state."""
    manager = _manager
    if manager is None or not manager.has_state():
        return
    await manager.shutdown()
