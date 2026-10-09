"""EventHub: the single /ws connection that feeds every phone session.

Backend facts this relies on (analysis sections 3.4 and 9):

- Frames are bare resource JSON with no event-type field, so the payload
  shape decides what an event is.
- Every event is published on two topics. A three-part prefix such as
  ``customer_id:<cid>:call`` matches both and delivers duplicates (measured),
  so only four-part event-type topics are subscribed.
- The server sends no subscribe acknowledgement and drops the connection on an
  invalid topic, so a connection that survives a short delay after subscribing
  is treated as ready.
- The server pings every 10 seconds; the websockets library answers.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import json
import logging
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect as ws_connect

from voipbin_mcp.phone import PhoneConfig

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class CallEvent:
    id: str
    status: str
    direction: str
    flow_id: str = ""
    destination: dict | None = None
    source: dict | None = None
    hangup_reason: str = ""
    hangup_by: str = ""
    groupcall_id: str = ""


@dataclasses.dataclass(frozen=True)
class TranscriptEvent:
    id: str
    transcribe_id: str
    direction: str
    message: str


@dataclasses.dataclass(frozen=True)
class InterimEvent:
    id: str
    transcribe_id: str
    direction: str
    message: str


Event = CallEvent | TranscriptEvent | InterimEvent


def subscription_topics(customer_id: str) -> list[str]:
    """The five four-part event-type topics (never a three-part prefix)."""
    prefix = f"customer_id:{customer_id}"
    return [
        f"{prefix}:call:call_created",
        f"{prefix}:call:call_progressing",
        f"{prefix}:call:call_hangup",
        f"{prefix}:transcript:transcript_created",
        f"{prefix}:transcribe:transcribe_speech_interim",
    ]


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def normalize(payload: Any) -> Event | None:
    """Classify a bare /ws payload by its shape; None means ignore it."""
    if not isinstance(payload, dict):
        return None
    if "offset_ms" in payload:
        return TranscriptEvent(
            id=_s(payload.get("id")),
            transcribe_id=_s(payload.get("transcribe_id")),
            direction=_s(payload.get("direction")),
            message=_s(payload.get("message")),
        )
    if "streaming_id" in payload:
        return InterimEvent(
            id=_s(payload.get("id")),
            transcribe_id=_s(payload.get("transcribe_id")),
            direction=_s(payload.get("direction")),
            message=_s(payload.get("message")),
        )
    if "status" in payload and payload.get("direction") in ("incoming", "outgoing"):
        return CallEvent(
            id=_s(payload.get("id")),
            status=_s(payload.get("status")),
            direction=_s(payload.get("direction")),
            flow_id=_s(payload.get("flow_id")),
            destination=payload.get("destination") or None,
            source=payload.get("source") or None,
            hangup_reason=_s(payload.get("hangup_reason")),
            hangup_by=_s(payload.get("hangup_by")),
            groupcall_id=_s(payload.get("groupcall_id")),
        )
    return None


def dedupe_key(event: Event) -> tuple:
    if isinstance(event, CallEvent):
        # Call events share the call id across statuses, so the status is part
        # of the key: progressing and hangup of one call are both delivered.
        return ("call", event.id, event.status)
    if isinstance(event, TranscriptEvent):
        return ("transcript", event.id)
    return ("interim", event.id)


def build_ws_url(base_url: str, api_key: str, auth_transport: str) -> tuple[str, dict]:
    """Derive the /ws URL and handshake headers from the REST settings."""
    parts = urlsplit(base_url.rstrip("/"))
    scheme = {"https": "wss", "http": "ws"}.get(parts.scheme, parts.scheme)
    path = parts.path.rstrip("/") + "/ws"
    query = ""
    headers: dict[str, str] = {}
    if auth_transport == "query":
        query = urlencode({"accesskey": api_key})
    else:
        headers["Cookie"] = f"accesskey={api_key}"
    return urlunsplit((scheme, parts.netloc, path, query, "")), headers


class EventHub:
    """One /ws connection, five topics, reconnect with backoff, dedupe, routing.

    ``on_event`` is called synchronously for every new event and must not
    block. ``on_reconnect`` is called (synchronously) after a reconnect becomes
    ready, so sessions can reconcile what they may have missed.
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        customer_id: str,
        on_event: Callable[[Event], None],
        on_reconnect: Callable[[], None] | None = None,
        config: PhoneConfig | None = None,
        connect: Callable = ws_connect,
    ):
        self.url = url
        self.headers = headers
        self.customer_id = customer_id
        self.topics = subscription_topics(customer_id)
        self._on_event = on_event
        self._on_reconnect = on_reconnect
        self.config = config or PhoneConfig()
        self._connect = connect
        self._seen: collections.OrderedDict[tuple, None] = collections.OrderedDict()
        self._ready = asyncio.Event()
        self.connected = False
        self.connections = 0

    async def wait_ready(self, timeout: float) -> bool:
        if self._ready.is_set():
            return True
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    def _set_disconnected(self) -> None:
        self.connected = False
        self._ready.clear()

    def handle_payload(self, payload: Any) -> None:
        """Normalise, dedupe and dispatch one decoded payload."""
        event = normalize(payload)
        if event is None:
            logger.debug("ignoring /ws payload: %r", payload)
            return
        key = dedupe_key(event)
        if key in self._seen:
            self._seen.move_to_end(key)
            return
        self._seen[key] = None
        while len(self._seen) > self.config.dedupe_size:
            self._seen.popitem(last=False)
        try:
            self._on_event(event)
        except Exception:  # pragma: no cover - a routing bug must not kill the socket
            logger.exception("phone event routing failed")

    def handle_raw(self, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.debug("ignoring non-JSON /ws frame")
            return
        if isinstance(data, list):
            for item in data:
                self.handle_payload(item)
        else:
            self.handle_payload(data)

    async def _mark_ready_later(self, reconnect: bool) -> None:
        await asyncio.sleep(self.config.ws_ready_delay)
        self.connected = True
        self._ready.set()
        if reconnect and self._on_reconnect is not None:
            try:
                self._on_reconnect()
            except Exception:  # pragma: no cover
                logger.exception("phone reconnect hook failed")

    async def run(self) -> None:
        """Connect and pump events until cancelled, reconnecting on failure."""
        backoff = self.config.ws_backoff_initial
        while True:
            ready_task: asyncio.Task | None = None
            try:
                async with self._connect(self.url, additional_headers=self.headers) as ws:
                    await ws.send(json.dumps({"type": "subscribe", "topics": self.topics}))
                    self.connections += 1
                    ready_task = asyncio.create_task(
                        self._mark_ready_later(reconnect=self.connections > 1)
                    )
                    async for raw in ws:
                        self.handle_raw(raw)
                    # A clean close still means we must reconnect.
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("phone /ws connection failed: %s", exc)
            finally:
                if ready_task is not None:
                    ready_task.cancel()
                if self.connected:
                    # The connection had become ready, so this is a fresh
                    # failure: start the backoff over.
                    backoff = self.config.ws_backoff_initial
                self._set_disconnected()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.config.ws_backoff_max)
