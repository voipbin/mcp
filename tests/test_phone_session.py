"""PhoneSession tests: listen/say/barge-in/hangup with injected events and respx."""

import asyncio
import json
import time

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
    interim,
    make_manager,
    ok,
    settle,
    teardown_manager,
    transcript,
    yielding_ok,
)
from voipbin_mcp.phone import PhoneError


@pytest.fixture(autouse=True)
def env(monkeypatch):
    voipbin_mcp.server._client = None
    monkeypatch.setenv("VOIPBIN_API_KEY", "test-key-123")
    monkeypatch.delenv("VOIPBIN_API_BASE_URL", raising=False)
    monkeypatch.delenv("VOIPBIN_AUTH_TRANSPORT", raising=False)
    yield
    voipbin_mcp.server._client = None


@pytest.fixture
async def api():
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        mock.get("/customer").mock(return_value=ok({"id": "cid"}))
        mock.get("/calls/c1").mock(return_value=ok({"id": "c1", "status": "progressing"}))
        mock.get("/transcripts").mock(return_value=ok({"result": []}))
        yield mock


@pytest.fixture
async def manager(api):
    m = make_manager()
    yield m
    await teardown_manager(m)


def later(delay, fn, *args):
    async def run():
        await asyncio.sleep(delay)
        fn(*args)

    return asyncio.get_running_loop().create_task(run())


class TestListen:
    async def test_two_transcripts_half_a_beat_apart_are_one_turn(self, api, manager):
        s = answered_session(manager)
        later(0.05, manager.on_event, transcript("t1", "I would like"))
        later(0.15, manager.on_event, transcript("t2", "a pizza"))
        result = await s.listen(2.0, 0.3)
        assert result["heard"] == "I would like a pizza"
        assert result["timed_out"] is False
        assert result["call_ended"] is False

    async def test_ongoing_interim_delays_the_end_of_turn(self, api, manager):
        s = answered_session(manager)
        later(0.02, manager.on_event, transcript("t1", "first part"))
        for i in range(6):
            later(0.1 + i * 0.08, manager.on_event, interim(f"still talking {i}"))
        later(0.62, manager.on_event, transcript("t2", "second part"))
        started = time.monotonic()
        result = await s.listen(5.0, 0.2)
        assert result["heard"] == "first part second part"
        assert time.monotonic() - started >= 0.6

    async def test_timeout_returns_empty_and_reconciles_once(self, api, manager):
        s = answered_session(manager)
        result = await s.listen(0.2, 0.3)
        assert result == {
            "heard": "",
            "timed_out": True,
            "truncated": False,
            "call_ended": False,
            "during_agent_speech": False,
        }
        assert api.routes  # sanity
        transcripts_route = [r for r in api.routes if r.pattern and "transcripts" in repr(r.pattern)][0]
        assert transcripts_route.call_count == 1
        params = transcripts_route.calls[0].request.url.params
        assert params["transcribe_id"] == "tr1" and params["page_size"] == "100"

    async def test_timeout_reconcile_recovers_a_dropped_transcript(self, api, manager):
        api.get("/transcripts").mock(
            side_effect=[
                ok({"result": [{"id": "t9", "message": "lost words", "direction": "in"}], "next_page_token": "p2"}),
                ok({"result": [{"id": "t10", "message": "more", "direction": "in"}], "next_page_token": ""}),
            ]
        )
        s = answered_session(manager)
        result = await s.listen(0.1, 0.05)
        assert result["heard"] == "lost words more"
        assert result["timed_out"] is False

    async def test_reconcile_at_the_hard_cap_returns_recovered_speech_untruncated(self, api, manager):
        api.get("/transcripts").mock(
            return_value=ok({"result": [{"id": "t9", "message": "lost words", "direction": "in"}]})
        )
        s = answered_session(manager)
        started = time.monotonic()
        # soft == hard (the say_and_listen cap): the reconcile runs at the cap.
        result = await s.listen(0.2, 2.0, hard_end=s.clock() + 0.2)
        assert result["heard"] == "lost words"
        assert result["timed_out"] is False
        assert result["truncated"] is False
        assert time.monotonic() - started < 1.0  # no extra end-of-silence wait

    async def test_reconcile_while_the_callee_is_still_talking_marks_truncated(self, api, manager):
        api.get("/transcripts").mock(
            return_value=ok({"result": [{"id": "t9", "message": "lost words", "direction": "in"}]})
        )
        s = answered_session(manager)
        for i in range(30):
            later(0.02 + i * 0.03, manager.on_event, interim(f"still going {i}"))
        result = await s.listen(0.1, 0.5)  # hard = 0.1 + grace 0.3
        assert result["heard"] == "lost words"
        assert result["timed_out"] is False
        assert result["truncated"] is True

    async def test_a_slow_reconcile_is_bounded(self, api):
        m = make_manager(reconcile_timeout=0.1)
        try:
            transcripts = api.get("/transcripts").mock(side_effect=yielding_ok({"result": []}, delay=3.0))
            s = answered_session(m)
            started = time.monotonic()
            result = await s.listen(0.1, 0.3)
            assert time.monotonic() - started < 0.6
            assert result["timed_out"] is True
            assert transcripts.call_count == 0  # cut off, not answered
        finally:
            await teardown_manager(m)

    async def test_interim_at_timeout_extends_up_to_the_grace(self, api, manager):
        s = answered_session(manager)
        later(0.15, manager.on_event, interim("hello the"))
        later(0.3, manager.on_event, transcript("t1", "hello there"))
        result = await s.listen(0.2, 0.05)
        assert result["heard"] == "hello there"
        assert result["timed_out"] is False

    async def test_endless_speech_is_cut_at_timeout_plus_grace_and_marked_truncated(self, api, manager):
        s = answered_session(manager)
        later(0.02, manager.on_event, transcript("t1", "blah"))
        for i in range(40):
            later(0.03 + i * 0.03, manager.on_event, interim(f"noise noise {i}"))
        started = time.monotonic()
        result = await s.listen(0.2, 0.2)  # hard cap = 0.2 + grace 0.3
        took = time.monotonic() - started
        assert result["heard"] == "blah"
        assert result["truncated"] is True
        assert 0.45 <= took < 1.0

    async def test_call_end_returns_immediately_with_the_buffer(self, api, manager):
        s = answered_session(manager)
        later(0.02, manager.on_event, transcript("t1", "goodbye"))
        later(0.05, manager.on_event, call_event("c1", "hangup", hangup_reason="normal"))
        started = time.monotonic()
        result = await s.listen(10.0, 3.0)
        assert time.monotonic() - started < 1.0
        assert result["call_ended"] is True
        assert result["heard"] == "goodbye"

    async def test_transcript_during_agent_speech_is_flagged(self, api, manager):
        s = answered_session(manager)
        s.speaking_until = s.clock() + 5
        manager.on_event(transcript("t1", "echo"))
        s.speaking_until = s.clock()
        result = await s.listen(0.5, 0.05)
        assert result["during_agent_speech"] is True

    async def test_transcripts_for_other_transcribes_are_ignored(self, api, manager):
        s = answered_session(manager)
        manager.on_event(transcript("t1", "someone else", transcribe_id="other"))
        result = await s.listen(0.1, 0.05)
        assert result["heard"] == ""


class TestSttSilence:
    async def test_silent_seconds_appear_after_the_threshold(self, api):
        clock = ManualClock()
        m = make_manager(clock=clock, stt_silent_warn_seconds=240)
        try:
            s = answered_session(m)
            clock.advance(239)
            assert "stt_silent_seconds" not in s.status_fields()
            clock.advance(2)
            assert s.status_fields()["stt_silent_seconds"] == pytest.approx(241)
            m.on_event(interim("hello there"))
            assert "stt_silent_seconds" not in s.status_fields()
        finally:
            await teardown_manager(m)


class TestStartMedia:
    async def test_bodies_and_reconcile_of_early_transcripts(self, api, manager):
        tr = api.post("/transcribes").mock(return_value=ok({"id": "tr1", "provider": "aws"}))
        sp = api.post("/speakings").mock(return_value=ok({"id": "sp1"}))
        api.get("/transcripts").mock(
            return_value=ok({"result": [{"id": "early", "message": "hello?", "direction": "in"}]})
        )
        s = manager.new_session(direction="outgoing", language="ko-KR", voice_id="v1")
        s.call_id = "c1"
        manager.sessions["c1"] = s
        s.mark_answered()
        # Arrives before the transcribe id is known: the router drops it.
        manager.on_event(transcript("early", "hello?"))
        await s.start_media()
        assert body(tr.calls[0]) == {
            "reference_type": "call",
            "reference_id": "c1",
            "language": "ko-KR",
            "direction": "in",
            "provider": "aws",
        }
        assert body(sp.calls[0]) == {
            "reference_type": "call",
            "reference_id": "c1",
            "language": "ko-KR",
            "direction": "out",
            "voice_id": "v1",
        }
        assert [t["message"] for t in s.transcripts] == ["hello?"]
        manager.on_event(transcript("early", "hello?"))  # duplicate by id
        assert len(s.transcripts) == 1


class TestSayAndBargeIn:
    async def test_say_posts_each_piece_and_estimates(self, api, manager):
        say = api.post("/speakings/sp1/say").mock(return_value=ok({"id": "sp1"}))
        s = answered_session(manager)
        result = await s.say("Hello there.")
        assert body(say.calls[0]) == {"text": "Hello there."}
        assert result["queued"] is True and result["interrupted"] is False
        assert s.speaking_until > s.clock()

    async def test_barge_in_stops_speech_and_next_say_recreates_it(self, api, manager):
        api.post("/speakings/sp1/say").mock(return_value=ok({}))
        stop = api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        create = api.post("/speakings").mock(return_value=ok({"id": "sp2"}))
        say2 = api.post("/speakings/sp2/say").mock(return_value=ok({}))
        s = answered_session(manager)
        await s.say("A long sentence for the callee.")
        await asyncio.sleep(0.08)  # past say_at + barge_window_delay
        manager.on_event(interim("wait a"))
        assert s.speaking_id is None
        assert s.unreported_barge_in is True
        await settle(manager)
        assert stop.call_count == 1
        assert s.stale_speaking_ids == []
        await s.say("Sorry, go ahead.")
        assert create.call_count == 1
        assert body(create.calls[0])["direction"] == "out"
        assert say2.call_count == 1
        assert s.consume_barge_in() is True
        assert s.consume_barge_in() is False  # reported once

    async def test_short_interims_do_not_barge_in(self, api, manager):
        api.post("/speakings/sp1/say").mock(return_value=ok({}))
        stop = api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        s = answered_session(manager)
        await s.say("Hello there.")
        await asyncio.sleep(0.08)
        manager.on_event(interim("uh"))  # one word, 2 chars
        assert stop.call_count == 0
        assert s.speaking_id == "sp1"

    async def test_interim_before_the_window_opens_is_ignored(self, api):
        m = make_manager(barge_window_delay=0.5)
        try:
            with respx.mock(base_url=BASE, assert_all_called=False) as mock:
                mock.post("/speakings/sp1/say").mock(return_value=ok({}))
                stop = mock.post("/speakings/sp1/stop").mock(return_value=ok({}))
                s = answered_session(m)
                await s.say("Hello there.")
                m.on_event(interim("I was already talking"))
                assert stop.call_count == 0
                assert s.speaking_id == "sp1"
        finally:
            await teardown_manager(m)

    async def test_barge_in_disabled_keeps_speaking_and_buffers(self, api, manager):
        api.post("/speakings/sp1/say").mock(return_value=ok({}))
        stop = api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        s = answered_session(manager)
        await s.say("Hello there.", barge_in=False)
        await asyncio.sleep(0.08)
        manager.on_event(interim("talking over you"))
        manager.on_event(transcript("t1", "talking over you"))
        await settle(manager)
        assert stop.call_count == 0
        assert s.speaking_id == "sp1"
        assert [t["message"] for t in s.transcripts] == ["talking over you"]

    async def test_next_say_awaits_the_pending_stop_before_recreating(self, api, manager):
        order = []

        async def slow_stop(request):
            order.append("stop-start")
            await asyncio.sleep(0.15)
            order.append("stop-done")
            return ok({})

        def create(request):
            order.append("create")
            return ok({"id": "sp2"})

        api.post("/speakings/sp1/say").mock(return_value=ok({}))
        api.post("/speakings/sp1/stop").mock(side_effect=slow_stop)
        api.post("/speakings").mock(side_effect=create)
        api.post("/speakings/sp2/say").mock(return_value=ok({}))
        s = answered_session(manager)
        await s.say("Hello there.")
        await asyncio.sleep(0.08)
        manager.on_event(interim("hold on"))
        await s.say("Yes?")
        assert order == ["stop-start", "stop-done", "create"]

    async def test_barge_in_mid_multi_piece_say_stops_the_rest_without_retry(self, api):
        m = make_manager(barge_window_delay=0.0, estimate_seconds=lambda t: 5.0)
        try:
            with respx.mock(base_url=BASE, assert_all_called=False) as mock:
                calls = []

                def on_say(request):
                    calls.append(json.loads(request.content)["text"][:5])
                    if len(calls) == 1:
                        # The callee talks while the first piece is in flight.
                        s.speaking_until = s.clock() + 5
                        m.on_event(interim("stop please"))
                    return ok({})

                mock.post("/speakings/sp1/say").mock(side_effect=on_say)
                mock.post("/speakings/sp1/stop").mock(return_value=ok({}))
                create = mock.post("/speakings").mock(return_value=ok({"id": "sp2"}))
                s = answered_session(m)
                text = ("Sentence one is here. " * 300).strip()  # ~6600 bytes => 2 pieces
                result = await s.say(text)
                assert result["pieces"] == 2
                assert result["interrupted"] is True
                assert len(calls) == 1  # no second piece
                assert create.call_count == 0  # no retry on a new speaking
                await settle(m)
        finally:
            await teardown_manager(m)

    async def test_barge_in_while_the_say_post_is_in_flight(self, api, manager):
        """The generation check after the POST: no estimate for a stopped piece."""
        api.post("/speakings/sp1/say").mock(return_value=ok({}))
        stop = api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        s = answered_session(manager)
        await s.say("First sentence for the callee.")
        await asyncio.sleep(0.08)  # inside the barge-in window of the first say

        async def in_flight(request):
            await asyncio.sleep(0.01)
            manager.on_event(interim("hold on please"))  # callee talks now
            await asyncio.sleep(0.01)
            return ok({})

        api.post("/speakings/sp1/say").mock(side_effect=in_flight)
        result = await s.say("Second sentence.")
        assert result["interrupted"] is True
        assert result["sent_pieces"] == 0
        assert result["estimated_seconds"] == 0.0
        assert s.speaking_until <= s.clock()  # not extended for a stopped piece
        await settle(manager)
        assert stop.call_count == 1

    async def test_say_error_caused_by_a_barge_in_stop_is_interrupted_not_raised(self, api, manager):
        api.post("/speakings/sp1/say").mock(return_value=ok({}))
        api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        s = answered_session(manager)
        await s.say("First sentence for the callee.")
        await asyncio.sleep(0.08)

        async def stopped_under_us(request):
            manager.on_event(interim("hold on please"))
            await asyncio.sleep(0.01)
            return err(404, "speaking not found")

        api.post("/speakings/sp1/say").mock(side_effect=stopped_under_us)
        result = await s.say("Second sentence.")
        assert result["interrupted"] is True
        await settle(manager)

    async def test_say_error_without_a_barge_in_is_raised(self, api, manager):
        from voipbin_mcp.client import VoIPbinAPIError

        api.post("/speakings/sp1/say").mock(return_value=err(500, "tts down"))
        s = answered_session(manager)
        with pytest.raises(VoIPbinAPIError):
            await s.say("Hello.")

    async def test_speaking_created_after_the_call_ended_is_stopped(self, api, manager):
        stop = api.post("/speakings/sp2/stop").mock(side_effect=yielding_ok())

        async def create(request):
            await asyncio.sleep(0.01)
            manager.on_event(call_event("c1", "hangup", hangup_reason="normal"))
            return ok({"id": "sp2"})

        api.post("/speakings").mock(side_effect=create)
        s = answered_session(manager, speaking_id=None)
        result = await s.say("Hello.")
        assert result["interrupted"] is True
        assert stop.call_count == 1
        assert s.speaking_id is None and s.stale_speaking_ids == []

    async def test_stale_speaking_is_stopped_again_before_the_one_create_retry(self, api, manager):
        api.post("/speakings/sp1/say").mock(return_value=ok({}))
        stop = api.post("/speakings/sp1/stop").mock(side_effect=[err(500), ok({})])
        create = api.post("/speakings").mock(
            side_effect=[
                err(500, "an active speaking session already exists for this reference"),
                ok({"id": "sp2"}),
            ]
        )
        api.post("/speakings/sp2/say").mock(return_value=ok({}))
        s = answered_session(manager)
        await s.say("Hello there.")
        await asyncio.sleep(0.08)
        manager.on_event(interim("hold on"))
        await settle(manager)
        assert s.stale_speaking_ids == ["sp1"]  # first stop failed
        await s.say("Go on.")
        assert stop.call_count == 2
        assert create.call_count == 2
        assert s.speaking_id == "sp2"
        assert s.stale_speaking_ids == []

    async def test_create_failure_without_stale_ids_is_not_retried(self, api, manager):
        create = api.post("/speakings").mock(return_value=err(500, "already exists"))
        s = answered_session(manager, speaking_id=None)
        with pytest.raises(Exception):
            await s.say("Hello.")
        assert create.call_count == 1

    async def test_wait_spoken_reports_still_speaking_past_the_cap(self, api):
        m = make_manager(estimate_seconds=lambda t: 0.5)
        try:
            with respx.mock(base_url=BASE, assert_all_called=False) as mock:
                mock.post("/speakings/sp1/say").mock(return_value=ok({}))
                s = answered_session(m)
                await s.say("Hello there.")
                assert await s.wait_spoken(0.1) is True
                assert await s.wait_spoken(2.0) is False
        finally:
            await teardown_manager(m)


class TestHangup:
    async def test_speaking_stop_failure_is_ignored(self, api, manager):
        stop = api.post("/speakings/sp1/stop").mock(return_value=err(500))
        hang = api.post("/calls/c1/hangup").mock(return_value=ok({"id": "c1"}))
        s = answered_session(manager)
        await s.hangup()
        assert stop.call_count == 1
        assert hang.call_count == 1
        assert s.ended and s.ended_reason == "agent_hangup"

    async def test_already_hung_up_call_is_fine(self, api, manager):
        api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        api.post("/calls/c1/hangup").mock(return_value=err(404, "not found"))
        s = answered_session(manager)
        await s.hangup()
        assert s.ended

    async def test_call_hangup_event_stops_the_speaking(self, api, manager):
        stop = api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        s = answered_session(manager)
        manager.on_event(call_event("c1", "hangup", hangup_reason="normal", hangup_by="remote"))
        await settle(manager)
        assert s.ended and s.ended_reason == "normal" and s.hangup_by == "remote"
        assert stop.call_count == 1

    async def test_say_after_hangup_reports_call_ended(self, api, manager):
        api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        s = answered_session(manager)
        manager.on_event(call_event("c1", "hangup"))
        with pytest.raises(PhoneError) as excinfo:
            await s.say("Hello?")
        assert excinfo.value.reason == "call_ended"
        await settle(manager)


class TestRepeatedCancellation:
    async def test_cleanup_http_is_sent_under_cancel_scope_cancel(self, api, manager):
        """anyio re-delivers cancellation at every await; cleanup must still run."""
        stop = api.post("/speakings/sp1/stop").mock(side_effect=yielding_ok())
        hang = api.post("/calls/c1/hangup").mock(side_effect=yielding_ok())
        s = answered_session(manager)

        async def hang_up_in_cancelled_scope():
            with anyio.CancelScope() as scope:
                scope.cancel()
                await s.hangup()

        async with anyio.create_task_group() as tg:
            tg.start_soon(hang_up_in_cancelled_scope)
            await anyio.sleep(0.01)
            tg.cancel_scope.cancel()
        assert stop.call_count == 1
        assert hang.call_count == 1

    async def test_hangup_tool_cancelled_mid_cleanup_still_hangs_up(self, api, manager):
        from voipbin_mcp.tools.phone import phone_hangup

        entered = asyncio.Event()
        stop = api.post("/speakings/sp1/stop").mock(side_effect=yielding_ok(entered=entered))
        hang = api.post("/calls/c1/hangup").mock(side_effect=yielding_ok())
        answered_session(manager)
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_hangup, "c1")
            await entered.wait()
            tg.cancel_scope.cancel()
        assert stop.call_count == 1
        assert hang.call_count == 1

    async def test_cancel_during_the_stale_stop_before_a_retry_still_stops(self, api, manager):
        """_stop_speaking's own shield: the say retry path is cancellable."""
        from voipbin_mcp.tools.phone import phone_say

        entered = asyncio.Event()
        create = api.post("/speakings").mock(return_value=err(500, "already active"))
        stop = api.post("/speakings/sp1/stop").mock(side_effect=yielding_ok(entered=entered))
        s = answered_session(manager, speaking_id=None)
        s.stale_speaking_ids = ["sp1"]
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_say, "c1", "Hello.")
            await entered.wait()
            tg.cancel_scope.cancel()
        assert stop.call_count == 1
        assert s.stale_speaking_ids == []
        assert create.call_count == 1  # no retry after the cancellation

    async def test_cancel_during_speaking_creation_still_records_the_id(self, api, manager):
        """The created speaking must be owned (and so stopped on hangup)."""
        from voipbin_mcp.tools.phone import phone_say

        entered = asyncio.Event()
        api.post("/speakings").mock(side_effect=yielding_ok({"id": "sp2"}, entered=entered))
        say = api.post("/speakings/sp2/say").mock(side_effect=yielding_ok())
        stop = api.post("/speakings/sp2/stop").mock(side_effect=yielding_ok())
        hang = api.post("/calls/c1/hangup").mock(side_effect=yielding_ok())
        s = answered_session(manager, speaking_id=None)
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_say, "c1", "Hello.")
            await entered.wait()
            tg.cancel_scope.cancel()
        assert s.speaking_id == "sp2"
        assert say.call_count == 0
        await s.hangup()
        assert stop.call_count == 1 and hang.call_count == 1

    async def test_cancelled_listen_keeps_the_call_and_releases_the_lock(self, api, manager):
        from voipbin_mcp.tools.phone import phone_listen

        hang = api.post("/calls/c1/hangup").mock(return_value=ok({}))
        s = answered_session(manager)
        async with anyio.create_task_group() as tg:
            tg.start_soon(phone_listen, "c1", 60, 800)
            await anyio.sleep(0.1)
            assert s.lock.locked()
            tg.cancel_scope.cancel()
        assert not s.lock.locked()
        assert not s.ended
        assert hang.call_count == 0
        assert s.active_ops == 0

    async def test_cancelled_say_stops_sending_pieces(self, api):
        m = make_manager()
        try:
            with respx.mock(base_url=BASE, assert_all_called=False) as mock:
                started = asyncio.Event()
                entered = []

                async def slow_say(request):
                    entered.append(1)
                    started.set()
                    await asyncio.sleep(5)
                    return ok({})

                mock.post("/speakings/sp1/say").mock(side_effect=slow_say)
                s = answered_session(m)
                from voipbin_mcp.tools.phone import phone_say

                async with anyio.create_task_group() as tg:
                    tg.start_soon(phone_say, "c1", ("Sentence one is here. " * 300).strip())
                    await started.wait()
                    tg.cancel_scope.cancel()
                assert entered == [1]  # the second piece was never sent
                assert not s.lock.locked()
                assert not s.ended
        finally:
            await teardown_manager(m)


class TestToolTurns:
    async def test_say_and_listen_separates_earlier_speech(self, api, manager):
        from voipbin_mcp.tools.phone import phone_say_and_listen

        api.post("/speakings/sp1/say").mock(return_value=ok({}))
        answered_session(manager)
        manager.on_event(transcript("t0", "are you there"))
        later(0.25, manager.on_event, transcript("t1", "yes I am"))
        result = json.loads(await phone_say_and_listen("c1", "Hello?", 2, 300))
        assert result["earlier_heard"] == "are you there"
        assert result["heard"] == "yes I am"
        assert result["still_speaking"] is False
        assert result["barge_in"] is False
        assert result["events_connected"] is False  # hub never started here

    async def test_listen_timeout_counts_from_the_estimated_end_of_speech(self, api):
        from voipbin_mcp.tools.phone import phone_say_and_listen

        m = make_manager(estimate_seconds=lambda t: 0.6)
        try:
            with respx.mock(base_url=BASE, assert_all_called=False) as mock:
                mock.post("/speakings/sp1/say").mock(return_value=ok({}))
                mock.get("/calls/c1").mock(return_value=ok({"status": "progressing"}))
                mock.get("/transcripts").mock(return_value=ok({"result": []}))
                answered_session(m)
                started = time.monotonic()
                result = json.loads(await phone_say_and_listen("c1", "Hello?", 1, 300))
                took = time.monotonic() - started
                assert result["timed_out"] is True
                assert took >= 1.55  # 0.6 speech + 1.0 listen, not 1.0 alone
        finally:
            await teardown_manager(m)

    async def test_say_and_listen_cap_includes_the_wait_for_the_lock(self, api):
        from voipbin_mcp.tools.phone import phone_say_and_listen

        m = make_manager(say_and_listen_max_block=1.0, reconcile_timeout=0.1, listen_grace_seconds=0.1)
        try:
            with respx.mock(base_url=BASE, assert_all_called=False) as mock:
                mock.post("/speakings/sp1/say").mock(return_value=ok({}))
                mock.get("/calls/c1").mock(return_value=ok({"status": "progressing"}))
                mock.get("/transcripts").mock(return_value=ok({"result": []}))
                s = answered_session(m)
                await s.lock.acquire()
                asyncio.get_running_loop().call_later(0.4, s.lock.release)
                started = time.monotonic()
                result = json.loads(await phone_say_and_listen("c1", "Hi.", 30, 300))
                took = time.monotonic() - started
                assert result["timed_out"] is True
                # The 1.0 s cap counts from the call, not from the lock: 0.4 s
                # lock wait, 0.2 s speech, listening up to 0.9 s (0.1 s is the
                # reconcile reserve). Counting from the lock would give 1.3 s.
                assert 0.8 <= took < 1.1, took
        finally:
            await teardown_manager(m)

    async def test_very_long_speech_returns_still_speaking_without_listening(self, api):
        from voipbin_mcp.tools.phone import phone_say_and_listen

        m = make_manager(estimate_seconds=lambda t: 500.0)
        try:
            with respx.mock(base_url=BASE, assert_all_called=False) as mock:
                mock.post("/speakings/sp1/say").mock(return_value=ok({}))
                answered_session(m)
                started = time.monotonic()
                result = json.loads(await phone_say_and_listen("c1", "A very long story.", 30, 800))
                assert time.monotonic() - started < 1.0
                assert result["still_speaking"] is True
                assert result["heard"] == ""
        finally:
            await teardown_manager(m)

    async def test_phone_say_wait_reports_still_speaking(self, api):
        from voipbin_mcp.tools.phone import phone_say

        m = make_manager(estimate_seconds=lambda t: 5.0, say_wait_max=0.1)
        try:
            with respx.mock(base_url=BASE, assert_all_called=False) as mock:
                mock.post("/speakings/sp1/say").mock(return_value=ok({}))
                answered_session(m)
                result = json.loads(await phone_say("c1", "Hello there.", wait=True))
                assert result["queued"] is True
                assert result["still_speaking"] is True
        finally:
            await teardown_manager(m)

    async def test_barge_in_after_say_without_wait_is_reported_by_the_next_listen(self, api, manager):
        from voipbin_mcp.tools.phone import phone_listen, phone_say

        api.post("/speakings/sp1/say").mock(return_value=ok({}))
        api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        answered_session(manager)
        first = json.loads(await phone_say("c1", "Hello there."))
        assert "barge_in" not in first
        await asyncio.sleep(0.08)
        manager.on_event(interim("excuse me"))
        manager.on_event(transcript("t1", "excuse me"))
        second = json.loads(await phone_listen("c1", 2, 300))
        assert second["barge_in"] is True
        assert second["heard"] == "excuse me"
        third = json.loads(await phone_listen("c1", 1, 300))
        assert third["barge_in"] is False
        await settle(manager)

    async def test_ended_session_answers_call_ended(self, api, manager):
        from voipbin_mcp.tools.phone import phone_listen, phone_status

        api.post("/speakings/sp1/stop").mock(return_value=ok({}))
        answered_session(manager)
        manager.on_event(transcript("t1", "bye"))
        manager.on_event(call_event("c1", "hangup", hangup_reason="normal"))
        result = json.loads(await phone_listen("c1", 30, 800))
        assert result["call_ended"] is True and result["heard"] == "bye"
        status = json.loads(await phone_status("c1"))
        assert status["state"] == "ended" and status["ended_reason"] == "normal"
        await settle(manager)

    async def test_unknown_call_id_is_an_error_result(self, api, manager):
        from voipbin_mcp.tools.phone import phone_listen

        result = json.loads(await phone_listen("nope", 30, 800))
        assert result["reason"] == "unknown_call"


def test_http_response_helper_shapes():
    assert isinstance(ok(), httpx.Response)
