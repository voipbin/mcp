"""EventHub tests against a real local websockets server (no network beyond loopback)."""

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from voipbin_mcp.phone import PhoneConfig
from voipbin_mcp.phone.events import (
    CallEvent,
    EventHub,
    InterimEvent,
    TranscriptEvent,
    build_ws_url,
    normalize,
    subscription_topics,
)

CID = "cid-1"

TRANSCRIPT = {
    "id": "t1",
    "transcribe_id": "tr1",
    "direction": "in",
    "message": "hello",
    "offset_ms": 1200,
    "tm_create": "2026-10-07T00:00:00Z",
}
INTERIM = {
    "id": "i1",
    "customer_id": CID,
    "transcribe_id": "tr1",
    "streaming_id": "s1",
    "direction": "in",
    "message": "hel",
}
CALL_PROGRESSING = {
    "id": "c1",
    "status": "progressing",
    "direction": "outgoing",
    "flow_id": "f1",
    "source": {"type": "tel", "target": "+1"},
    "destination": {"type": "tel", "target": "+2"},
    "groupcall_id": "00000000-0000-0000-0000-000000000000",
}
CALL_HANGUP = dict(CALL_PROGRESSING, status="hangup", hangup_reason="normal", hangup_by="remote")


def fast_config(**overrides) -> PhoneConfig:
    values = dict(ws_ready_delay=0.05, ws_backoff_initial=0.05, ws_backoff_max=0.1)
    values.update(overrides)
    return PhoneConfig(**values)


class FakeServer:
    """Records handshakes and subscribe messages; sends scripted frames."""

    def __init__(self, frames=(), close_first=False, close_after_subscribe=False):
        self.frames = list(frames)
        self.close_first = close_first
        self.close_after_subscribe = close_after_subscribe
        self.connections = []
        self.subscribes = []
        self._server = None
        self.port = None

    async def handler(self, ws):
        self.connections.append(
            {"path": ws.request.path, "cookie": ws.request.headers.get("Cookie")}
        )
        index = len(self.connections)
        message = json.loads(await ws.recv())
        self.subscribes.append(message)
        if self.close_after_subscribe:
            await ws.close()
            return
        if self.close_first and index == 1:
            await asyncio.sleep(0.1)
            await ws.close()
            return
        await asyncio.sleep(0.08)
        for frame in self.frames:
            await ws.send(json.dumps(frame) if not isinstance(frame, str) else frame)
        try:
            await ws.wait_closed()
        except Exception:
            pass

    async def __aenter__(self):
        self._server = await serve(self.handler, "127.0.0.1", 0).__aenter__()
        self.port = list(self._server.sockets)[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        await self._server.__aexit__(*exc)


async def run_hub(hub, until, timeout=3.0):
    task = asyncio.create_task(hub.run())
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while not until():
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("condition not reached")
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestUrlAndTopics:
    def test_https_becomes_wss_with_ws_path_and_cookie(self):
        url, headers = build_ws_url("https://api.voipbin.net/v1.0", "k1", "cookie")
        assert url == "wss://api.voipbin.net/v1.0/ws"
        assert headers == {"Cookie": "accesskey=k1"}

    def test_http_becomes_ws_and_query_transport_uses_the_query(self):
        url, headers = build_ws_url("http://localhost:8080/v1.0/", "k1", "query")
        assert url == "ws://localhost:8080/v1.0/ws?accesskey=k1"
        assert headers == {}

    def test_query_transport_encodes_the_access_key(self):
        url, _ = build_ws_url("https://api.voipbin.net/v1.0", "a b&c=d+/", "query")
        assert url == "wss://api.voipbin.net/v1.0/ws?accesskey=a+b%26c%3Dd%2B%2F"
        from urllib.parse import parse_qs, urlsplit

        assert parse_qs(urlsplit(url).query) == {"accesskey": ["a b&c=d+/"]}

    def test_five_four_part_topics_and_no_three_part_prefix(self):
        topics = subscription_topics(CID)
        assert len(topics) == 5
        assert all(len(t.split(":")) == 4 for t in topics)
        assert f"customer_id:{CID}:call" not in topics
        assert set(topics) == {
            f"customer_id:{CID}:call:call_created",
            f"customer_id:{CID}:call:call_progressing",
            f"customer_id:{CID}:call:call_hangup",
            f"customer_id:{CID}:transcript:transcript_created",
            f"customer_id:{CID}:transcribe:transcribe_speech_interim",
        }


class TestNormalize:
    def test_transcript(self):
        ev = normalize(TRANSCRIPT)
        assert ev == TranscriptEvent(id="t1", transcribe_id="tr1", direction="in", message="hello")

    def test_interim(self):
        ev = normalize(INTERIM)
        assert ev == InterimEvent(id="i1", transcribe_id="tr1", direction="in", message="hel")

    def test_call(self):
        ev = normalize(CALL_HANGUP)
        assert isinstance(ev, CallEvent)
        assert (ev.id, ev.status, ev.direction, ev.flow_id) == ("c1", "hangup", "outgoing", "f1")
        assert ev.hangup_reason == "normal" and ev.hangup_by == "remote"
        assert ev.destination == {"type": "tel", "target": "+2"}

    @pytest.mark.parametrize(
        "payload",
        [
            {"id": "g1", "status": "hangup", "call_ids": []},  # groupcall: no direction
            {"id": "x", "status": "progressing", "direction": "in"},  # transcribe resource
            {"hello": "world"},
            ["not", "a", "dict"],
        ],
    )
    def test_anything_else_is_ignored(self, payload):
        assert normalize(payload) is None


class TestHubOverWebsocket:
    @pytest.mark.asyncio
    async def test_cookie_handshake_and_subscribe_message(self):
        async with FakeServer() as server:
            url, headers = build_ws_url(f"http://127.0.0.1:{server.port}/v1.0", "secret", "cookie")
            hub = EventHub(url, headers, CID, on_event=lambda e: None, config=fast_config())
            await run_hub(hub, lambda: hub.connected)
        assert server.connections[0]["cookie"] == "accesskey=secret"
        assert server.connections[0]["path"] == "/v1.0/ws"
        assert server.subscribes[0] == {"type": "subscribe", "topics": subscription_topics(CID)}

    @pytest.mark.asyncio
    async def test_query_transport_sends_the_key_in_the_query_only(self):
        async with FakeServer() as server:
            url, headers = build_ws_url(f"http://127.0.0.1:{server.port}/v1.0", "secret", "query")
            hub = EventHub(url, headers, CID, on_event=lambda e: None, config=fast_config())
            await run_hub(hub, lambda: hub.connected)
        assert server.connections[0]["path"] == "/v1.0/ws?accesskey=secret"
        assert server.connections[0]["cookie"] is None

    @pytest.mark.asyncio
    async def test_events_are_normalised_deduped_and_dispatched(self):
        frames = [
            CALL_PROGRESSING,
            CALL_PROGRESSING,  # duplicate (dual topic / owner routing)
            CALL_HANGUP,  # same id, new status: must still be delivered
            TRANSCRIPT,
            TRANSCRIPT,
            INTERIM,
            dict(INTERIM, id="i2", message="hello wor"),
            {"id": "g1", "status": "progressing"},  # ignored
            "not json",
        ]
        received = []
        async with FakeServer(frames=frames) as server:
            url, headers = build_ws_url(f"http://127.0.0.1:{server.port}/v1.0", "k", "cookie")
            hub = EventHub(url, headers, CID, on_event=received.append, config=fast_config())
            await run_hub(hub, lambda: len(received) >= 5)
            await asyncio.sleep(0.05)
        kinds = [(type(e).__name__, e.id, getattr(e, "status", "")) for e in received]
        assert kinds == [
            ("CallEvent", "c1", "progressing"),
            ("CallEvent", "c1", "hangup"),
            ("TranscriptEvent", "t1", ""),
            ("InterimEvent", "i1", ""),
            ("InterimEvent", "i2", ""),
        ]

    @pytest.mark.asyncio
    async def test_reconnect_resubscribes_and_triggers_reconcile(self):
        reconnects = []
        async with FakeServer(close_first=True) as server:
            url, headers = build_ws_url(f"http://127.0.0.1:{server.port}/v1.0", "k", "cookie")
            hub = EventHub(
                url,
                headers,
                CID,
                on_event=lambda e: None,
                on_reconnect=lambda: reconnects.append(1),
                config=fast_config(),
            )
            await run_hub(hub, lambda: reconnects)
        assert len(server.connections) >= 2
        assert server.subscribes[0] == server.subscribes[1]
        assert reconnects == [1]
        assert hub.connections >= 2

    @pytest.mark.asyncio
    async def test_a_connection_closed_right_after_subscribe_is_never_ready(self):
        # The server answers an invalid topic by closing; there is no ack.
        async with FakeServer(close_after_subscribe=True) as server:
            url, headers = build_ws_url(f"http://127.0.0.1:{server.port}/v1.0", "k", "cookie")
            hub = EventHub(url, headers, CID, on_event=lambda e: None, config=fast_config(ws_ready_delay=0.2))
            task = asyncio.create_task(hub.run())
            try:
                assert await hub.wait_ready(0.5) is False
                assert len(server.connections) >= 2  # it keeps retrying
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        assert hub.connected is False

    @pytest.mark.asyncio
    async def test_dedupe_cache_is_bounded(self):
        received = []
        hub = EventHub("ws://unused", {}, CID, on_event=received.append, config=fast_config(dedupe_size=2))
        for i in range(3):
            hub.handle_payload(dict(TRANSCRIPT, id=f"t{i}"))
        hub.handle_payload(dict(TRANSCRIPT, id="t0"))  # evicted, so delivered again
        assert [e.id for e in received] == ["t0", "t1", "t2", "t0"]
