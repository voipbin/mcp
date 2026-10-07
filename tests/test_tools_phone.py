"""Phone tool tests: validation, outgoing/groupcall, incoming, limits, watchdog, shutdown."""

import asyncio
import json
import signal

import anyio
import httpx
import pytest
import respx

import voipbin_mcp.server
from phone_fakes import (
    BASE,
    ManualClock,
    answered_session,
    body,
    call_event,
    err,
    make_manager,
    ok,
    settle,
    teardown_manager,
)
from voipbin_mcp.phone import NIL_UUID, PhoneError
from voipbin_mcp.phone.manager import current_manager, reset_manager, shutdown_if_started
from voipbin_mcp.tools import phone as phone_tools

MARKER = json.dumps({"voipbin_mcp": "incoming", "number_id": "n1", "previous_call_flow_id": "orig"})
PHONE_TOOLS = [
    "phone_call_start",
    "phone_incoming_configure",
    "phone_wait_incoming",
    "phone_say_and_listen",
    "phone_say",
    "phone_listen",
    "phone_hangup",
    "phone_status",
]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    voipbin_mcp.server._client = None
    monkeypatch.setenv("VOIPBIN_API_KEY", "test-key-123")
    monkeypatch.delenv("VOIPBIN_API_BASE_URL", raising=False)
    monkeypatch.delenv("VOIPBIN_AUTH_TRANSPORT", raising=False)
    yield
    voipbin_mcp.server._client = None
    reset_manager(None)


@pytest.fixture
async def api():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/customer").mock(return_value=ok({"id": "cid"}))
        mock.get("/transcripts").mock(return_value=ok({"result": []}))
        mock.post("/transcribes").mock(return_value=ok({"id": "tr1"}))
        mock.post("/speakings").mock(return_value=ok({"id": "sp1"}))
        yield mock


@pytest.fixture
async def manager(api):
    m = make_manager()
    yield m
    await teardown_manager(m)


def emit_later(manager, delay, event):
    async def run():
        await asyncio.sleep(delay)
        manager.on_event(event)

    return asyncio.get_running_loop().create_task(run())


async def wait_until(predicate, timeout=2.0):
    with anyio.fail_after(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


# --------------------------------------------------------------- registration


class TestRegistration:
    async def test_exactly_the_eight_tools_exist_with_cost_first_lines(self):
        from voipbin_mcp.server import mcp

        tools = {t.name: t for t in await mcp.list_tools() if t.name.startswith("phone_")}
        assert sorted(tools) == sorted(PHONE_TOOLS)
        for name, tool in tools.items():
            first = (tool.description or "").strip().splitlines()[0].lower()
            assert "live phone conversation" in first, name
            assert "bill" in first or "cost" in first, name

    def test_decorator_form_is_exact(self):
        import inspect

        source = inspect.getsource(phone_tools)
        assert source.count("@mcp.tool()") == 8


# ----------------------------------------------------------------- validation


class TestValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"destination_type": "agent"},
            {"max_duration_seconds": 29},
            {"max_duration_seconds": 3601},
            {"answer_timeout_seconds": 4},
            {"answer_timeout_seconds": 61},
            {"source_number": " "},
            {"destination_target": ""},
        ],
    )
    async def test_call_start_rejects_without_any_request(self, api, manager, kwargs):
        args = dict(source_number="+15550001", destination_type="tel", destination_target="+15550002")
        args.update(kwargs)
        with pytest.raises(ValueError):
            await phone_tools.phone_call_start(**args)
        assert api.calls.call_count == 0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"greeting": ""},
            {"greeting": "   "},
            {"timeout_seconds": 0},
            {"timeout_seconds": 601},
            {"max_duration_seconds": 10},
        ],
    )
    async def test_wait_incoming_rejects_without_any_request(self, api, manager, kwargs):
        with pytest.raises(ValueError):
            await phone_tools.phone_wait_incoming("n1", **kwargs)
        assert api.calls.call_count == 0

    @pytest.mark.parametrize(
        "call",
        [
            lambda: phone_tools.phone_say_and_listen("c1", ""),
            lambda: phone_tools.phone_say_and_listen("c1", "x" * 20001),
            lambda: phone_tools.phone_say_and_listen("c1", "hi", listen_timeout_seconds=121),
            lambda: phone_tools.phone_say_and_listen("c1", "hi", end_silence_ms=299),
            lambda: phone_tools.phone_say_and_listen("c1", "hi", end_silence_ms=3001),
            lambda: phone_tools.phone_say("c1", " "),
            lambda: phone_tools.phone_say("", "hi"),
            lambda: phone_tools.phone_listen("c1", timeout_seconds=0),
            lambda: phone_tools.phone_listen("c1", timeout_seconds=121),
            lambda: phone_tools.phone_listen("c1", end_silence_ms=100),
            lambda: phone_tools.phone_hangup(""),
            lambda: phone_tools.phone_incoming_configure("", True),
        ],
    )
    async def test_turn_tools_reject_without_any_request(self, api, manager, call):
        with pytest.raises(ValueError):
            await call()
        assert api.calls.call_count == 0

    async def test_korean_text_limit_counts_bytes(self, api, manager):
        with pytest.raises(ValueError):
            await phone_tools.phone_say("c1", "가" * 6667)  # 20001 bytes


# ------------------------------------------------------------------- outgoing


class TestOutgoing:
    async def test_tel_call_body_answer_and_media(self, api, manager):
        post = api.post("/calls").mock(return_value=ok({"calls": [{"id": "c1"}], "groupcalls": []}))
        api.get("/calls/c1").mock(return_value=ok({"id": "c1", "status": "dialing"}))
        emit_later(manager, 0.02, call_event("c1", "progressing"))
        result = json.loads(
            await phone_tools.phone_call_start(
                "+15550001", "tel", "+15550002", language="ko-KR", max_duration_seconds=120
            )
        )
        assert result["call_id"] == "c1" and result["status"] == "answered"
        assert result["max_duration_seconds"] == 120 and result["hint"]
        assert body(post.calls[0]) == {
            "source": {"type": "tel", "target": "+15550001"},
            "destinations": [{"type": "tel", "target": "+15550002"}],
            "actions": [{"type": "sleep", "option": {"duration": 120000}}],
        }
        session = manager.sessions["c1"]
        assert session.state == "answered"
        assert session.transcribe_id == "tr1" and session.speaking_id == "sp1"
        assert manager.slots_in_use == 1

    async def test_event_before_the_post_response_is_replayed(self, api, manager):
        def respond(request):
            manager.on_event(call_event("c1", "progressing"))
            return ok({"calls": [{"id": "c1"}]})

        api.post("/calls").mock(side_effect=respond)
        result = await manager.call_start(
            source_number="+1", destination_type="sip", destination_target="sip:a@b",
            language="en-US", voice_id="", max_duration_seconds=60, answer_timeout_seconds=5,
        )
        assert result["call_id"] == "c1"

    async def test_extension_uses_target_name_and_confirms_the_answering_leg(self, api, manager):
        post = api.post("/calls").mock(
            return_value=ok({"calls": [], "groupcalls": [{"id": "g1", "call_ids": ["l1", "l2"]}]})
        )
        api.get("/groupcalls/g1").mock(return_value=ok({"id": "g1", "status": "progressing", "call_ids": ["l1", "l2"]}))
        api.get("/calls/l1").mock(return_value=ok({"status": "ringing"}))
        api.get("/calls/l2").mock(return_value=ok({"status": "ringing"}))
        tr = api.post("/transcribes").mock(return_value=ok({"id": "tr1"}))
        emit_later(manager, 0.02, call_event("l1", "ringing"))
        emit_later(manager, 0.03, call_event("l2", "progressing"))
        result = json.loads(await phone_tools.phone_call_start("+15550001", "extension", "ext-100"))
        assert body(post.calls[0])["destinations"] == [{"type": "extension", "target_name": "ext-100"}]
        assert body(post.calls[0])["actions"][0]["option"]["duration"] == 3600000
        assert result["call_id"] == "l2"
        assert body(tr.calls[0])["reference_id"] == "l2"
        assert "l2" in manager.sessions and not manager.pending

    async def test_empty_call_ids_are_filled_by_one_groupcall_get(self, api, manager):
        api.post("/calls").mock(return_value=ok({"groupcalls": [{"id": "g1", "call_ids": []}]}))
        group = api.get("/groupcalls/g1").mock(return_value=ok({"id": "g1", "call_ids": ["l1"]}))
        emit_later(manager, 0.01, call_event("l1", "progressing"))
        result = await manager.call_start(
            source_number="+1", destination_type="extension", destination_target="e",
            language="en-US", voice_id="", max_duration_seconds=60, answer_timeout_seconds=5,
        )
        assert result["call_id"] == "l1"
        assert group.call_count == 1

    async def test_answer_call_id_from_the_poll_confirms_a_leg(self, api, manager):
        api.post("/calls").mock(return_value=ok({"groupcalls": [{"id": "g1", "call_ids": ["l1"]}]}))
        api.get("/groupcalls/g1").mock(return_value=ok({"status": "progressing", "answer_call_id": "l1"}))
        result = await manager.call_start(
            source_number="+1", destination_type="extension", destination_target="e",
            language="en-US", voice_id="", max_duration_seconds=60, answer_timeout_seconds=5,
        )
        assert result["call_id"] == "l1"

    async def test_groupcall_hangup_status_fails_and_cleans_up(self, api, manager):
        api.post("/calls").mock(return_value=ok({"groupcalls": [{"id": "g1", "call_ids": ["l1"]}]}))
        api.get("/groupcalls/g1").mock(return_value=ok({"status": "hangup", "call_ids": ["l1"]}))
        api.get("/calls/l1").mock(return_value=err(404))
        ghang = api.post("/groupcalls/g1/hangup").mock(return_value=ok({}))
        with pytest.raises(PhoneError) as excinfo:
            await manager.call_start(
                source_number="+1", destination_type="extension", destination_target="e",
                language="en-US", voice_id="", max_duration_seconds=60, answer_timeout_seconds=5,
            )
        assert excinfo.value.reason == "call_failed"
        assert ghang.call_count == 1
        assert manager.slots_in_use == 0

    async def test_every_candidate_hung_up_fails(self, api, manager):
        api.post("/calls").mock(return_value=ok({"groupcalls": [{"id": "g1", "call_ids": ["l1", "l2"]}]}))
        api.get("/groupcalls/g1").mock(return_value=ok({"status": "progressing"}))
        api.get("/calls/l1").mock(return_value=ok({"status": "hangup"}))
        api.get("/calls/l2").mock(return_value=ok({"status": "hangup"}))
        api.post("/groupcalls/g1/hangup").mock(return_value=ok({}))
        emit_later(manager, 0.01, call_event("l1", "hangup"))
        emit_later(manager, 0.02, call_event("l2", "hangup"))
        with pytest.raises(PhoneError) as excinfo:
            await manager.call_start(
                source_number="+1", destination_type="extension", destination_target="e",
                language="en-US", voice_id="", max_duration_seconds=60, answer_timeout_seconds=5,
            )
        assert excinfo.value.reason == "call_failed"

    async def test_single_call_hangup_reports_the_reason(self, api, manager):
        api.post("/calls").mock(return_value=ok({"calls": [{"id": "c1"}]}))
        api.get("/calls/c1").mock(return_value=ok({"status": "dialing"}))
        api.post("/calls/c1/hangup").mock(return_value=err(404))
        emit_later(manager, 0.01, call_event("c1", "hangup", hangup_reason="busy"))
        result = json.loads(await phone_tools.phone_call_start("+1555", "tel", "+1666"))
        assert result["reason"] == "call_failed"
        assert result["hangup_reason"] == "busy"
        assert manager.slots_in_use == 0

    async def test_answer_timeout_hangs_up_and_reports_no_answer(self, api, manager):
        api.post("/calls").mock(return_value=ok({"calls": [{"id": "c1"}]}))
        api.get("/calls/c1").mock(return_value=ok({"status": "ringing"}))
        hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
        with pytest.raises(PhoneError) as excinfo:
            await manager.call_start(
                source_number="+1", destination_type="tel", destination_target="+2",
                language="en-US", voice_id="", max_duration_seconds=60, answer_timeout_seconds=0.2,
            )
        assert excinfo.value.reason == "no_answer"
        assert hang.call_count == 1
        assert manager.sessions["c1"].ended_reason == "no_answer"

    async def test_rejected_post_releases_the_slot(self, api, manager):
        api.post("/calls").mock(return_value=err(400, "invalid source"))
        from voipbin_mcp.client import VoIPbinAPIError

        with pytest.raises(VoIPbinAPIError):
            await phone_tools.phone_call_start("+1555", "tel", "+1666")
        assert manager.slots_in_use == 0
        assert manager.sessions == {} and not manager.pending

    async def test_media_failure_hangs_up(self, api, manager):
        api.post("/calls").mock(return_value=ok({"calls": [{"id": "c1"}]}))
        api.post("/transcribes").mock(return_value=err(400, "bad language"))
        hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
        emit_later(manager, 0.01, call_event("c1", "progressing"))
        from voipbin_mcp.client import VoIPbinAPIError

        with pytest.raises(VoIPbinAPIError):
            await phone_tools.phone_call_start("+1555", "tel", "+1666")
        assert hang.call_count == 1


class TestOutgoingCancellation:
    """Cancel through a task group's cancel scope, as FastMCP does (repeated)."""

    async def test_cancel_during_post_registers_then_hangs_up_the_call(self, api, manager):
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def slow_post(request):
            entered.set()
            await gate.wait()
            return ok({"calls": [{"id": "c1"}]})

        api.post("/calls").mock(side_effect=slow_post)
        hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_tools.phone_call_start, "+1555", "tel", "+1666")
            await entered.wait()
            tg.cancel_scope.cancel()
            gate.set()
        assert hang.call_count == 1
        assert manager.sessions["c1"].ended
        assert manager.slots_in_use == 0

    async def test_cancel_right_after_post_hangs_up(self, api, manager):
        post = api.post("/calls").mock(return_value=ok({"calls": [{"id": "c1"}]}))
        api.get("/calls/c1").mock(return_value=ok({"status": "dialing"}))
        hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_tools.phone_call_start, "+1555", "tel", "+1666")
            await wait_until(lambda: post.called)
            tg.cancel_scope.cancel()
        assert hang.call_count == 1

    async def test_cancel_while_waiting_for_a_groupcall_answer_hangs_up_every_leg(self, api, manager):
        api.post("/calls").mock(return_value=ok({"groupcalls": [{"id": "g1", "call_ids": ["l1"]}]}))
        api.get("/groupcalls/g1").mock(return_value=ok({"status": "progressing", "call_ids": ["l1", "l2"]}))
        # l1 keeps ringing until it has been hung up; l2 was never created.
        l1_state = {"status": "ringing"}
        api.get("/calls/l1").mock(side_effect=lambda r: ok(dict(l1_state)))
        api.get("/calls/l2").mock(return_value=err(404))
        ghang = api.post("/groupcalls/g1/hangup").mock(return_value=ok({}))

        def hang_l1(request):
            l1_state["status"] = "hangup"
            return ok({})

        lhang = api.post("/calls/l1/hangup").mock(side_effect=hang_l1)
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_tools.phone_call_start, "+1555", "extension", "e1")
            await wait_until(lambda: manager.pending)
            await asyncio.sleep(0.02)
            tg.cancel_scope.cancel()
        assert ghang.call_count == 1
        assert lhang.call_count == 1
        assert manager.slots_in_use == 0
        assert not manager.pending

    async def test_cancel_after_answer_confirmation_hangs_up_the_call(self, api, manager):
        api.post("/calls").mock(return_value=ok({"calls": [{"id": "c1"}]}))
        started = asyncio.Event()

        async def slow_transcribe(request):
            started.set()
            await asyncio.sleep(5)
            return ok({"id": "tr1"})

        api.post("/transcribes").mock(side_effect=slow_transcribe)
        hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
        emit_later(manager, 0.01, call_event("c1", "progressing"))
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_tools.phone_call_start, "+1555", "tel", "+1666")
            await started.wait()
            tg.cancel_scope.cancel()
        assert hang.call_count == 1


# --------------------------------------------------------------------- limits


class TestLimits:
    async def test_concurrent_starts_cannot_exceed_the_limit(self, api):
        m = make_manager(max_sessions=1)
        try:
            gate = asyncio.Event()

            async def slow_post(request):
                await gate.wait()
                return ok({"calls": [{"id": "c1"}]})

            post = api.post("/calls").mock(side_effect=slow_post)
            api.get("/calls/c1").mock(return_value=ok({"status": "dialing"}))
            emit = None
            first = asyncio.create_task(phone_tools.phone_call_start("+1", "tel", "+2"))
            await asyncio.sleep(0.05)
            second = json.loads(await phone_tools.phone_call_start("+1", "tel", "+3"))
            assert second["reason"] == "session_limit"
            gate.set()
            emit = emit_later(m, 0.02, call_event("c1", "progressing"))
            first_result = json.loads(await first)
            assert first_result["status"] == "answered"
            assert post.call_count == 1
            await emit
        finally:
            await teardown_manager(m)

    async def test_ended_sessions_are_kept_for_sixty_seconds_outside_the_limit(self, api):
        clock = ManualClock()
        m = make_manager(clock=clock, max_sessions=1)
        try:
            api.post("/speakings/sp1/stop").mock(return_value=ok({}))
            s = answered_session(m)
            s.slot_held = True
            m.slots_in_use = 1
            m.on_event(call_event("c1", "hangup"))
            assert m.slots_in_use == 0
            clock.advance(59)
            m.supervise_once()
            assert "c1" in m.sessions
            result = json.loads(await phone_tools.phone_listen("c1", 30, 800))
            assert result["call_ended"] is True
            clock.advance(2)
            m.supervise_once()
            assert "c1" not in m.sessions
            await settle(m)
        finally:
            await teardown_manager(m)


# ----------------------------------------------------------------- supervisor


class TestSupervisor:
    async def _manager(self, **kw):
        clock = ManualClock()
        return make_manager(clock=clock, **kw), clock

    async def test_idle_watchdog_hangs_up(self, api):
        m, clock = await self._manager(idle_hangup_seconds=300)
        try:
            api.post("/speakings/sp1/stop").mock(return_value=ok({}))
            hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
            s = answered_session(m)
            clock.advance(299)
            m.supervise_once()
            assert not s.ended
            clock.advance(2)
            m.supervise_once()
            m.supervise_once()  # must not double-spawn
            await settle(m)
            assert s.ended and s.ended_reason == "idle_timeout"
            assert hang.call_count == 1
        finally:
            await teardown_manager(m)

    async def test_a_blocking_listen_counts_as_activity(self, api):
        m, clock = await self._manager(idle_hangup_seconds=300)
        try:
            s = answered_session(m)
            s.active_ops = 1
            clock.advance(1000)
            m.supervise_once()
            assert not s.ended
        finally:
            await teardown_manager(m)

    async def test_deadline_hangs_up(self, api):
        m, clock = await self._manager()
        try:
            api.post("/speakings/sp1/stop").mock(return_value=ok({}))
            hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
            s = answered_session(m, max_duration_seconds=60)
            clock.advance(30)
            s.touch()
            m.supervise_once()
            assert not s.ended
            clock.advance(31)
            s.touch()
            m.supervise_once()
            await settle(m)
            assert s.ended_reason == "max_duration"
            assert hang.call_count == 1
        finally:
            await teardown_manager(m)

    async def test_supervisor_restarts_a_dead_hub_and_still_enforces_limits(self, api):
        m, clock = await self._manager(idle_hangup_seconds=10)
        try:
            api.post("/speakings/sp1/stop").mock(return_value=ok({}))
            hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
            await m.ensure_started()
            hub = m.hub
            await asyncio.sleep(0)
            runs_before = hub.runs

            async def boom():
                raise RuntimeError("hub crashed")

            m._hub_task.cancel()
            m._hub_task = asyncio.get_running_loop().create_task(boom())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            hub.connected = False
            assert m.events_connected() is False
            s = answered_session(m)
            clock.advance(11)
            m.supervise_once()
            await settle(m)
            assert s.ended_reason == "idle_timeout"
            assert hang.call_count == 1
            assert not m._hub_task.done()  # restarted
            await asyncio.sleep(0)
            assert hub.runs == runs_before + 1
        finally:
            await teardown_manager(m)

    async def test_reconnect_reconciles_every_live_session(self, api, manager):
        call = api.get("/calls/c1").mock(return_value=ok({"status": "progressing"}))
        api.get("/transcripts").mock(
            return_value=ok({"result": [{"id": "t5", "message": "missed", "direction": "in"}]})
        )
        s = answered_session(manager)
        manager._on_reconnect()
        await settle(manager)
        assert call.call_count == 1
        assert [t["message"] for t in s.transcripts] == ["missed"]

    async def test_status_reports_events_connected(self, api, manager):
        await manager.ensure_started()
        answered_session(manager)
        status = json.loads(await phone_tools.phone_status())
        assert status["events_connected"] is True
        assert status["sessions"][0]["call_id"] == "c1"
        one = json.loads(await phone_tools.phone_status("c1"))
        assert one["state"] == "answered" and "remaining_seconds" in one


# ------------------------------------------------------------------- incoming


def mock_number(api, flow_id="mf1", previous="orig"):
    api.get("/numbers/n1").mock(return_value=ok({"id": "n1", "number": "+15550001", "call_flow_id": flow_id}))
    detail = json.dumps({"voipbin_mcp": "incoming", "number_id": "n1", "previous_call_flow_id": previous})
    api.get(f"/flows/{flow_id}").mock(return_value=ok({"id": flow_id, "detail": detail}))


def ringing(call_id, flow_id="mf1", direction="incoming"):
    return call_event(
        call_id, "ringing", direction=direction, flow_id=flow_id,
        source={"type": "tel", "target": "+15559999"},
        destination={"type": "tel", "target": "+15550001"},
    )


async def wait_args(**kw):
    args = dict(
        number_id="n1", timeout_seconds=2.0, greeting="Hello.", greeting_language="en-US",
        language="en-US", voice_id="", max_duration_seconds=3600,
    )
    args.update(kw)
    return args


class TestIncoming:
    async def test_answers_with_talk_then_starts_media(self, api, manager):
        mock_number(api)
        talk = api.post("/calls/x1/talk").mock(return_value=httpx.Response(200))  # empty body
        api.get("/calls/x1").mock(return_value=ok({"status": "ringing"}))
        tr = api.post("/transcribes").mock(return_value=ok({"id": "tr1"}))

        def progress(request):
            emit_later(manager, 0.01, call_event("x1", "progressing", direction="incoming"))
            return httpx.Response(200)

        talk.mock(side_effect=progress)
        emit_later(manager, 0.05, ringing("x1"))
        result = json.loads(
            await phone_tools.phone_wait_incoming("n1", timeout_seconds=5, greeting="Hi there.", greeting_language="ko-KR")
        )
        assert result["call_id"] == "x1" and result["status"] == "answered"
        assert result["caller"] == {"type": "tel", "target": "+15559999"}
        assert body(talk.calls[0]) == {"text": "Hi there.", "language": "ko-KR"}
        # media is created only after the answer
        assert tr.calls[0].request is not None
        assert manager.waiters == {}
        assert manager.slots_in_use == 1

    async def test_other_flows_and_outgoing_calls_are_not_matched(self, api, manager):
        mock_number(api)
        emit_later(manager, 0.02, ringing("o1", flow_id="other-flow"))  # same number, other flow
        emit_later(manager, 0.03, ringing("o2", direction="outgoing"))
        result = await manager.wait_incoming(**await wait_args(timeout_seconds=0.2))
        assert result["timed_out"] is True
        assert manager.slots_in_use == 0
        assert "o1" not in manager.unclaimed  # a foreign flow is never rejected

    async def test_number_without_the_mcp_flow_is_refused(self, api, manager):
        api.get("/numbers/n1").mock(return_value=ok({"call_flow_id": "plain"}))
        api.get("/flows/plain").mock(return_value=ok({"id": "plain", "detail": "my flow"}))
        result = json.loads(await phone_tools.phone_wait_incoming("n1", timeout_seconds=1))
        assert result["reason"] == "not_configured"

    async def test_second_waiter_on_a_number_is_refused(self, api, manager):
        mock_number(api)
        first = asyncio.create_task(manager.wait_incoming(**await wait_args(timeout_seconds=0.5)))
        await wait_until(lambda: manager.waiters)
        with pytest.raises(PhoneError) as excinfo:
            await manager.wait_incoming(**await wait_args())
        assert excinfo.value.reason == "waiter_exists"
        await first

    async def test_session_limit_applies_to_waiters(self, api):
        m = make_manager(max_sessions=1)
        try:
            mock_number(api)
            m.slots_in_use = 1
            with pytest.raises(PhoneError) as excinfo:
                await m.wait_incoming(**await wait_args())
            assert excinfo.value.reason == "session_limit"
            assert m.waiters == {}
        finally:
            await teardown_manager(m)

    async def test_call_that_arrived_just_before_the_first_wait_is_taken(self, api, manager):
        mock_number(api)
        await manager.ensure_started()
        manager.on_event(ringing("x1"))  # flow not yet known: only buffered
        assert "x1" not in manager.unclaimed
        api.get("/calls/x1").mock(return_value=ok({"status": "ringing"}))

        def progress(request):
            emit_later(manager, 0.01, call_event("x1", "progressing", direction="incoming"))
            return httpx.Response(200)

        api.post("/calls/x1/talk").mock(side_effect=progress)
        result = await manager.wait_incoming(**await wait_args())
        assert result["call_id"] == "x1"

    async def test_caller_gone_before_answer_keeps_waiting_for_the_next_call(self, api, manager):
        mock_number(api)
        api.post("/calls/x1/talk").mock(return_value=err(404, "call not found"))
        api.get("/calls/x1").mock(return_value=ok({"status": "hangup"}))

        def progress(request):
            emit_later(manager, 0.01, call_event("x2", "progressing", direction="incoming"))
            return httpx.Response(200)

        api.post("/calls/x2/talk").mock(side_effect=progress)
        api.get("/calls/x2").mock(return_value=ok({"status": "ringing"}))
        emit_later(manager, 0.02, ringing("x1"))
        emit_later(manager, 0.1, ringing("x2"))
        result = await manager.wait_incoming(**await wait_args())
        assert result["call_id"] == "x2"
        assert manager.sessions["x1"].ended_reason == "caller_hangup"
        assert manager.slots_in_use == 1

    async def test_talk_failure_on_a_live_call_hangs_up_and_errors(self, api, manager):
        mock_number(api)
        api.post("/calls/x1/talk").mock(return_value=err(500, "tts failed"))
        api.get("/calls/x1").mock(return_value=ok({"status": "ringing"}))
        hang = api.post("/calls/x1/hangup").mock(return_value=ok({}))
        emit_later(manager, 0.02, ringing("x1"))
        from voipbin_mcp.client import VoIPbinAPIError

        with pytest.raises(VoIPbinAPIError):
            await manager.wait_incoming(**await wait_args())
        assert hang.call_count == 1
        assert manager.slots_in_use == 0

    async def test_cancel_before_detection_removes_the_waiter_and_slot(self, api, manager):
        mock_number(api)
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_tools.phone_wait_incoming, "n1")
            await wait_until(lambda: manager.waiters)
            tg.cancel_scope.cancel()
        assert manager.waiters == {}
        assert manager.slots_in_use == 0
        assert api.calls.call_count == 3  # customer, number, flow: nothing else

    async def test_cancel_after_talk_hangs_up(self, api, manager):
        mock_number(api)
        talk = api.post("/calls/x1/talk").mock(return_value=httpx.Response(200))
        api.get("/calls/x1").mock(return_value=ok({"status": "ringing"}))  # never progressing
        hang = api.post("/calls/x1/hangup").mock(return_value=ok({}))
        emit_later(manager, 0.02, ringing("x1"))
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_tools.phone_wait_incoming, "n1")
            await wait_until(lambda: talk.called)
            tg.cancel_scope.cancel()
        assert hang.call_count == 1
        assert manager.slots_in_use == 0


class TestUnclaimedIncoming:
    async def _known_flow(self, api, manager):
        mock_number(api)
        await manager.wait_incoming(**await wait_args(timeout_seconds=0.01))
        assert "mf1" in manager.incoming_flow_ids

    async def test_ringing_call_without_a_waiter_is_rejected_after_the_grace(self, api, manager):
        await self._known_flow(api, manager)
        get = api.get("/calls/u1").mock(return_value=ok({"status": "ringing"}))
        hang = api.post("/calls/u1/hangup").mock(return_value=ok({}))
        manager.on_event(ringing("u1"))
        await asyncio.sleep(0.05)
        assert hang.call_count == 0  # still inside the grace
        await asyncio.sleep(0.2)
        await settle(manager)
        assert get.call_count == 1
        assert hang.call_count == 1

    async def test_not_rejected_when_no_longer_ringing(self, api, manager):
        await self._known_flow(api, manager)
        api.get("/calls/u1").mock(return_value=ok({"status": "progressing"}))
        hang = api.post("/calls/u1/hangup").mock(return_value=ok({}))
        manager.on_event(ringing("u1"))
        await asyncio.sleep(0.25)
        await settle(manager)
        assert hang.call_count == 0

    async def test_progressing_or_hangup_cancels_the_timer(self, api, manager):
        await self._known_flow(api, manager)
        get = api.get("/calls/u1").mock(return_value=ok({"status": "ringing"}))
        get2 = api.get("/calls/u2").mock(return_value=ok({"status": "ringing"}))
        manager.on_event(ringing("u1"))
        manager.on_event(ringing("u2"))
        manager.on_event(call_event("u1", "progressing", direction="incoming"))
        manager.on_event(call_event("u2", "hangup", direction="incoming"))
        await asyncio.sleep(0.25)
        assert get.call_count == 0 and get2.call_count == 0
        assert manager.unclaimed == {}

    async def test_outgoing_only_process_never_rejects(self, api, manager):
        hang = api.post("/calls/u1/hangup").mock(return_value=ok({}))
        manager.on_event(ringing("u1"))
        await asyncio.sleep(0.25)
        assert manager.unclaimed == {}
        assert hang.call_count == 0

    async def test_new_waiter_takes_an_unclaimed_call(self, api, manager):
        await self._known_flow(api, manager)
        manager.config.unclaimed_incoming_grace_seconds = 5.0
        hang = api.post("/calls/u1/hangup").mock(return_value=ok({}))

        def progress(request):
            emit_later(manager, 0.01, call_event("u1", "progressing", direction="incoming"))
            return httpx.Response(200)

        api.post("/calls/u1/talk").mock(side_effect=progress)
        manager.on_event(ringing("u1"))
        assert "u1" in manager.unclaimed
        result = await manager.wait_incoming(**await wait_args())
        assert result["call_id"] == "u1"
        assert manager.unclaimed == {}
        assert hang.call_count == 0


class TestIncomingConfigure:
    async def test_enable_replaces_the_flow_and_returns_the_previous(self, api, manager):
        api.get("/numbers/n1").mock(return_value=ok({"number": "+15550001", "call_flow_id": "orig"}))
        api.get("/flows/orig").mock(return_value=ok({"id": "orig", "detail": ""}))
        create = api.post("/flows").mock(return_value=ok({"id": "mf1"}))
        put = api.put("/numbers/n1/flow_ids").mock(return_value=ok({}))
        result = json.loads(await phone_tools.phone_incoming_configure("n1", True))
        assert result == {
            "number": "+15550001",
            "call_flow_id": "mf1",
            "previous_call_flow_id": "orig",
            "enabled": True,
        }
        sent = body(create.calls[0])
        assert sent["name"] == "voipbin-mcp incoming n1"
        assert json.loads(sent["detail"]) == {
            "voipbin_mcp": "incoming", "number_id": "n1", "previous_call_flow_id": "orig",
        }
        assert sent["actions"] == [{"type": "sleep", "option": {"duration": 3600000}}]
        assert body(put.calls[0]) == {"call_flow_id": "mf1"}

    async def test_enable_again_keeps_the_stored_previous(self, api, manager):
        mock_number(api)
        create = api.post("/flows").mock(return_value=ok({"id": "x"}))
        result = json.loads(await phone_tools.phone_incoming_configure("n1", True))
        assert result["previous_call_flow_id"] == "orig"
        assert result["call_flow_id"] == "mf1"
        assert create.call_count == 0

    async def test_enable_deletes_the_new_flow_when_the_put_fails(self, api, manager):
        api.get("/numbers/n1").mock(return_value=ok({"call_flow_id": NIL_UUID}))
        api.post("/flows").mock(return_value=ok({"id": "mf1"}))
        api.put("/numbers/n1/flow_ids").mock(return_value=err(500))
        delete = api.delete("/flows/mf1").mock(return_value=ok({}))
        from voipbin_mcp.client import VoIPbinAPIError

        with pytest.raises(VoIPbinAPIError):
            await phone_tools.phone_incoming_configure("n1", True)
        assert delete.call_count == 1

    async def test_disable_restores_the_stored_flow_and_deletes_the_mcp_flow(self, api, manager):
        mock_number(api)
        api.get("/flows/orig").mock(return_value=ok({"id": "orig"}))
        put = api.put("/numbers/n1/flow_ids").mock(return_value=ok({}))
        delete = api.delete("/flows/mf1").mock(return_value=ok({}))
        result = json.loads(await phone_tools.phone_incoming_configure("n1", False))
        assert result["call_flow_id"] == "orig" and result["enabled"] is False
        assert body(put.calls[0]) == {"call_flow_id": "orig"}
        assert delete.call_count == 1

    async def test_disable_with_an_explicit_restore_target(self, api, manager):
        mock_number(api)
        api.get("/flows/other").mock(return_value=ok({"id": "other"}))
        put = api.put("/numbers/n1/flow_ids").mock(return_value=ok({}))
        api.delete("/flows/mf1").mock(return_value=ok({}))
        await phone_tools.phone_incoming_configure("n1", False, restore_call_flow_id="other")
        assert body(put.calls[0]) == {"call_flow_id": "other"}

    async def test_disable_without_a_stored_flow_needs_clear(self, api, manager):
        mock_number(api, previous=None)
        put = api.put("/numbers/n1/flow_ids").mock(return_value=ok({}))
        api.delete("/flows/mf1").mock(return_value=ok({}))
        result = json.loads(await phone_tools.phone_incoming_configure("n1", False))
        assert result["reason"] == "no_previous_flow"
        assert put.call_count == 0
        await phone_tools.phone_incoming_configure("n1", False, clear=True)
        assert body(put.calls[0]) == {"call_flow_id": NIL_UUID}

    async def test_disable_refuses_a_missing_restore_flow(self, api, manager):
        mock_number(api)
        api.get("/flows/orig").mock(return_value=err(404))
        put = api.put("/numbers/n1/flow_ids").mock(return_value=ok({}))
        result = json.loads(await phone_tools.phone_incoming_configure("n1", False))
        assert result["reason"] == "restore_flow_missing"
        assert put.call_count == 0

    async def test_disable_refuses_a_foreign_flow(self, api, manager):
        api.get("/numbers/n1").mock(return_value=ok({"call_flow_id": "plain"}))
        api.get("/flows/plain").mock(return_value=ok({"detail": "x"}))
        result = json.loads(await phone_tools.phone_incoming_configure("n1", False))
        assert result["reason"] == "not_configured"

    async def test_disable_refuses_while_waiting_or_talking(self, api, manager):
        mock_number(api)
        waiting = asyncio.create_task(manager.wait_incoming(**await wait_args(timeout_seconds=0.3)))
        await wait_until(lambda: manager.waiters)
        result = json.loads(await phone_tools.phone_incoming_configure("n1", False))
        assert result["reason"] == "number_busy"
        await waiting
        s = answered_session(manager, call_id="x9")
        s.number_id = "n1"
        result = json.loads(await phone_tools.phone_incoming_configure("n1", False))
        assert result["reason"] == "number_busy"


# ------------------------------------------------------------------- shutdown


class TestShutdown:
    async def test_no_phone_state_is_a_no_op_without_a_client(self, api):
        reset_manager(None)
        await shutdown_if_started()
        assert voipbin_mcp.server._client is None
        phone_tools._manager()  # created lazily, still nothing started
        assert current_manager() is not None
        await shutdown_if_started()
        assert voipbin_mcp.server._client is None
        assert api.calls.call_count == 0

    async def test_main_finally_is_a_no_op_without_phone_state(self, api, monkeypatch):
        served = []

        async def serve():
            served.append(1)

        monkeypatch.setattr(voipbin_mcp.server.mcp, "run_stdio_async", serve)
        reset_manager(None)
        await voipbin_mcp.server._serve()
        assert served == [1]
        assert voipbin_mcp.server._client is None
        assert api.calls.call_count == 0

    async def test_main_finally_hangs_up_live_sessions(self, api, manager, monkeypatch):
        api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
        answered_session(manager)

        async def serve():
            return None

        monkeypatch.setattr(voipbin_mcp.server.mcp, "run_stdio_async", serve)
        await voipbin_mcp.server._serve()
        assert hang.call_count == 1

    async def test_shutdown_waits_for_an_inflight_post_and_rejects_unclaimed(self, api, manager):
        gate = asyncio.Event()

        async def slow_post(request):
            await gate.wait()
            return ok({"calls": [{"id": "c1"}]})

        api.post("/calls").mock(side_effect=slow_post)
        api.get("/calls/c1").mock(return_value=ok({"status": "dialing"}))
        hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
        # an unclaimed ringing call on a flow this process waited on
        manager.incoming_flow_ids.add("mf1")
        manager.config.unclaimed_incoming_grace_seconds = 60
        api.get("/calls/u1").mock(return_value=ok({"status": "ringing"}))
        uhang = api.post("/calls/u1/hangup").mock(return_value=ok({}))
        api.get("/calls/u2").mock(return_value=ok({"status": "progressing"}))
        u2hang = api.post("/calls/u2/hangup").mock(return_value=ok({}))

        starting = asyncio.create_task(phone_tools.phone_call_start("+1", "tel", "+2"))
        await wait_until(lambda: manager.inflight_posts == 1)
        manager.on_event(ringing("u1"))
        manager.on_event(ringing("u2"))
        emit = asyncio.get_running_loop().call_later(0.1, gate.set)
        await manager.shutdown()
        emit.cancel()
        assert hang.call_count >= 1  # registered after the POST, then hung up
        assert uhang.call_count == 1
        assert u2hang.call_count == 0  # answered elsewhere: never hung up
        starting.cancel()
        try:
            await starting
        except BaseException:
            pass

    async def test_signal_cleans_up_then_exits_and_ignores_a_second_signal(self, api):
        exits = []
        m = make_manager(exits=exits)
        try:
            api.post("/speakings/sp1/stop").mock(return_value=ok({}))
            hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
            answered_session(m)
            m._on_signal(signal.SIGTERM)
            task = m._signal_task
            m._on_signal(signal.SIGTERM)
            assert m._signal_task is task
            await task
            assert exits == [128 + signal.SIGTERM]
            assert hang.call_count == 1
        finally:
            await teardown_manager(m)

    async def test_signal_handlers_are_installed_only_on_first_phone_use(self, api):
        loop = asyncio.get_running_loop()
        m = make_manager()
        m._install_signals = True
        try:
            assert m._signals_installed is False
            await m.ensure_started()
            assert m._signals_installed is True
        finally:
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(signum)
            await teardown_manager(m)

    async def test_hangup_tool_works_while_a_listen_is_blocked(self, api, manager):
        api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
        api.get("/calls/c1").mock(return_value=ok({"status": "progressing"}))
        answered_session(manager)
        listening = asyncio.create_task(phone_tools.phone_listen("c1", 60, 800))
        await asyncio.sleep(0.05)
        result = json.loads(await phone_tools.phone_hangup("c1"))
        assert result["status"] == "ended"
        heard = json.loads(await asyncio.wait_for(listening, 1))
        assert heard["call_ended"] is True
        assert hang.call_count == 1
        again = json.loads(await phone_tools.phone_hangup("c1"))
        assert again["status"] == "ended" and hang.call_count == 1
