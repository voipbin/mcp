"""Live phone conversation tools: the agent itself speaks (TTS) and listens (STT).

The session machinery lives in ``voipbin_mcp.phone``; this module only
validates arguments and shapes results.
"""

from voipbin_mcp.phone import PhoneError
from voipbin_mcp.phone.manager import get_manager
from voipbin_mcp.server import format_response, get_client, mcp

MAX_CALL_SECONDS = 3600
MIN_CALL_SECONDS = 30
ANSWER_TIMEOUT_RANGE = (5, 60)
LISTEN_TIMEOUT_MAX = 120
WAIT_INCOMING_TIMEOUT_MAX = 600
END_SILENCE_MS_RANGE = (300, 3000)
MAX_SAY_TEXT_BYTES = 20000
DESTINATION_TYPES = ("tel", "sip", "extension")


def _manager():
    return get_manager(get_client)


def _check_range(name: str, value, low, high) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between {low} and {high}")
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}, got {value}")


def _check_text(text: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must not be empty")
    size = len(text.encode("utf-8"))
    if size > MAX_SAY_TEXT_BYTES:
        raise ValueError(
            f"text is {size} bytes; one call accepts at most {MAX_SAY_TEXT_BYTES} "
            "bytes of UTF-8. Split it across several calls."
        )


def _check_call_id(call_id: str) -> None:
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("call_id must not be empty")


def _error(exc: PhoneError) -> str:
    return format_response(exc.as_dict())


def _ended_result(session) -> dict:
    result = {
        "heard": " ".join(item["message"] for item in session.drain()),
        "barge_in": session.consume_barge_in(),
        "timed_out": False,
        "truncated": False,
        "call_ended": True,
        "during_agent_speech": False,
    }
    result.update(session.status_fields())
    return result


def _session_for_turn(call_id: str):
    # No touch here: every caller enters session.activity() right after,
    # without an await in between, and that is what refreshes the idle timer.
    session = _manager().get_session(call_id)
    if not session.ended and session.state != "answered":
        raise PhoneError("the call is not answered yet", "not_answered")
    return session


@mcp.tool()
async def phone_call_start(
    source_number: str,
    destination_type: str,
    destination_target: str,
    language: str = "en-US",
    voice_id: str = "",
    max_duration_seconds: int = 3600,
    answer_timeout_seconds: int = 60,
) -> str:
    """Start a live phone conversation: place a call that YOU will speak on and listen to. Costs money (the call, speech-to-text and text-to-speech are all billed).

    Dials the destination, waits for the answer, then starts transcription of
    the callee's voice and a text-to-speech channel towards them. When this
    returns status "answered", talk with phone_say_and_listen (one turn per
    call), and end with phone_hangup. If you stop calling tools for 5 minutes
    the call is hung up automatically, and it is always hung up at
    max_duration_seconds.

    Returns {"call_id", "status": "answered", "max_duration_seconds", "hint"},
    or {"error", "reason"} where reason is no_answer, call_failed (with
    hangup_reason when known), session_limit (at most 4 concurrent calls) or
    events_unavailable.

    The platform hangs up every call one hour after its channel is created, so a conversation can never run longer than that; this is why max_duration_seconds is capped at 3600.

    Args:
        source_number: Your VoIPbin number to call from (E.164, e.g. "+15551234567").
        destination_type: tel, sip or extension.
        destination_target: The phone number, SIP URI, or extension name to call.
        language: BCP-47 language of the conversation (e.g. "en-US", "ko-KR").
            Used for both speech recognition and speech synthesis.
        voice_id: Optional text-to-speech voice id. Empty uses the default
            voice for the language.
        max_duration_seconds: Maximum length of the conversation, 30 to 3600
            (default 3600). It cannot be extended after the call starts.
        answer_timeout_seconds: How long to wait for an answer, 5 to 60
            (default 60).
    """
    if destination_type not in DESTINATION_TYPES:
        raise ValueError(f"destination_type must be one of {', '.join(DESTINATION_TYPES)}")
    if not isinstance(source_number, str) or not source_number.strip():
        raise ValueError("source_number must not be empty")
    if not isinstance(destination_target, str) or not destination_target.strip():
        raise ValueError("destination_target must not be empty")
    _check_range("max_duration_seconds", max_duration_seconds, MIN_CALL_SECONDS, MAX_CALL_SECONDS)
    _check_range("answer_timeout_seconds", answer_timeout_seconds, *ANSWER_TIMEOUT_RANGE)
    try:
        result = await _manager().call_start(
            source_number=source_number.strip(),
            destination_type=destination_type,
            destination_target=destination_target.strip(),
            language=language,
            voice_id=voice_id,
            max_duration_seconds=int(max_duration_seconds),
            answer_timeout_seconds=int(answer_timeout_seconds),
        )
    except PhoneError as exc:
        return _error(exc)
    return format_response(result)


@mcp.tool()
async def phone_incoming_configure(
    number_id: str,
    enabled: bool,
    restore_call_flow_id: str = "",
    clear: bool = False,
) -> str:
    """Prepare (or release) a number for live phone conversations on INCOMING calls. Answered calls are billed (the call, speech-to-text and text-to-speech).

    enabled=true REPLACES the number's existing call flow with a flow that
    keeps callers ringing until phone_wait_incoming answers them, so incoming
    calls are only handled while an MCP server is running and waiting. The
    original call flow id is stored on the replacement flow, so calling this
    again, even from a new session, never loses it. Only one MCP process may
    wait on a number at a time.

    enabled=false restores the original call flow (or restore_call_flow_id)
    and deletes the replacement flow. It fails while this process is still
    waiting on, or talking on, the number.

    Returns {"number", "call_flow_id", "previous_call_flow_id", "enabled"}.
    For enabled=false, previous_call_flow_id is the MCP flow that was removed.

    Args:
        number_id: UUID of your VoIPbin number (see list_numbers).
        enabled: true to take over incoming calls, false to give the number back.
        restore_call_flow_id: With enabled=false, the call flow to restore
            instead of the stored one. It must exist.
        clear: With enabled=false and no stored previous flow, set true to
            leave the number without any call flow. Without it the request is
            refused, so incoming routing is never removed silently.
    """
    if not isinstance(number_id, str) or not number_id.strip():
        raise ValueError("number_id must not be empty")
    try:
        result = await _manager().incoming_configure(
            number_id.strip(), bool(enabled), restore_call_flow_id or "", bool(clear)
        )
    except PhoneError as exc:
        return _error(exc)
    return format_response(result)


@mcp.tool()
async def phone_wait_incoming(
    number_id: str,
    timeout_seconds: int = 120,
    greeting: str = "Hello.",
    greeting_language: str = "en-US",
    language: str = "en-US",
    voice_id: str = "",
    max_duration_seconds: int = 3600,
) -> str:
    """Wait for an incoming call and answer it for a live phone conversation. Costs money once a call is answered (the call, speech-to-text and text-to-speech are billed).

    The number must first be prepared with phone_incoming_configure. When a
    call arrives it is answered by playing the greeting, then transcription
    and text-to-speech start, and you continue with phone_say_and_listen.
    Returns {"call_id", "caller", "status": "answered"} or
    {"status": "timed_out", "timed_out": true}. For long waits call this
    repeatedly rather than with a large timeout.

    While this MCP server runs, an incoming call that nobody is waiting for is
    rejected after 15 seconds.

    When no MCP process is running, an incoming call to a configured number is never answered: it keeps ringing until the platform's one-hour call duration timeout.

    Args:
        number_id: UUID of the number prepared with phone_incoming_configure.
        timeout_seconds: How long to wait for a call, 1 to 600 (default 120).
        greeting: What the caller hears on answer. Required: playing it is
            what answers the call.
        greeting_language: Language of the greeting voice (e.g. "en-US").
        language: BCP-47 language of the conversation for speech recognition
            and synthesis.
        voice_id: Optional text-to-speech voice id for the conversation (not
            used for the greeting).
        max_duration_seconds: Maximum length of the conversation, 30 to 3600.
    """
    if not isinstance(number_id, str) or not number_id.strip():
        raise ValueError("number_id must not be empty")
    if not isinstance(greeting, str) or not greeting.strip():
        raise ValueError("greeting must not be empty: playing it is what answers the call")
    _check_range("timeout_seconds", timeout_seconds, 1, WAIT_INCOMING_TIMEOUT_MAX)
    _check_range("max_duration_seconds", max_duration_seconds, MIN_CALL_SECONDS, MAX_CALL_SECONDS)
    try:
        result = await _manager().wait_incoming(
            number_id=number_id.strip(),
            timeout_seconds=float(timeout_seconds),
            greeting=greeting.strip(),
            greeting_language=greeting_language,
            language=language,
            voice_id=voice_id,
            max_duration_seconds=int(max_duration_seconds),
        )
    except PhoneError as exc:
        return _error(exc)
    return format_response(result)


@mcp.tool()
async def phone_say_and_listen(
    call_id: str,
    text: str,
    listen_timeout_seconds: int = 30,
    end_silence_ms: int = 800,
    barge_in: bool = True,
) -> str:
    """Take one turn of a live phone conversation: say text, then listen for the reply. Speech synthesis and recognition are billed.

    Speaks text to the other party, then returns what they said after it.
    The turn ends once they stay silent for end_silence_ms. If they start
    talking over you and barge_in is true, your speech is stopped.

    Returns {"heard", "earlier_heard", "barge_in", "timed_out", "truncated",
    "call_ended", "during_agent_speech", "events_connected"}. earlier_heard
    is speech that was already buffered before you spoke. still_speaking is
    true when your text is too long to finish within this call; continue with
    phone_listen. during_agent_speech marks speech captured while you were
    talking (possibly an echo of your own voice). stt_silent_seconds appears
    when no speech has been recognised for a long time, which can mean
    transcription stopped.

    Args:
        call_id: The call_id from phone_call_start or phone_wait_incoming.
        text: What to say. At most 20000 bytes per call.
        listen_timeout_seconds: How long to wait for a reply after you finish
            speaking, 1 to 120 (default 30).
        end_silence_ms: Silence that ends their turn, 300 to 3000 (default 800).
        barge_in: Stop your speech when they talk over you (default true).
    """
    _check_call_id(call_id)
    _check_text(text)
    _check_range("listen_timeout_seconds", listen_timeout_seconds, 1, LISTEN_TIMEOUT_MAX)
    _check_range("end_silence_ms", end_silence_ms, *END_SILENCE_MS_RANGE)
    try:
        session = _session_for_turn(call_id)
        if session.ended:
            result = _ended_result(session)
            result["earlier_heard"] = ""
            return format_response(result)
        config = session.config
        # The 130 s cap covers the whole tool call, including the wait for
        # another tool holding the session lock, and keeps the listen's
        # reconcile budget inside it.
        started = session.clock()
        hard_end = started + config.say_and_listen_max_block - config.reconcile_timeout
        with session.activity():
            async with session.lock:
                earlier = " ".join(item["message"] for item in session.drain())
                try:
                    await session.say(text, bool(barge_in))
                except PhoneError as exc:
                    if exc.reason != "call_ended":
                        raise
                    result = _ended_result(session)
                    result["earlier_heard"] = earlier
                    return format_response(result)
                start_at = max(session.clock(), session.speaking_until)
                if start_at >= hard_end:
                    result = {
                        "heard": "",
                        "timed_out": False,
                        "truncated": False,
                        "call_ended": session.ended,
                        "during_agent_speech": False,
                        "still_speaking": session.speaking_until > session.clock(),
                    }
                else:
                    result = await session.listen(
                        min(float(listen_timeout_seconds), hard_end - start_at),
                        end_silence_ms / 1000.0,
                        base=start_at,
                        hard_end=hard_end,
                    )
                    result["still_speaking"] = False
                result["earlier_heard"] = earlier
                result["barge_in"] = session.consume_barge_in()
                result.update(session.status_fields())
    except PhoneError as exc:
        return _error(exc)
    return format_response(result)


@mcp.tool()
async def phone_say(call_id: str, text: str, wait: bool = False, barge_in: bool = True) -> str:
    """Speak in a live phone conversation without listening afterwards. Speech synthesis is billed.

    Queues text for the other party. With wait=true it returns when your
    speech is estimated to have finished (or when they talk over you), at
    most 120 seconds; still_speaking is then true if it had not finished.
    Their speech meanwhile is buffered for the next phone_listen.

    Returns {"queued", "estimated_seconds"} plus "barge_in": true when they
    talked over you since the last result, and "still_speaking" with wait.

    Args:
        call_id: The call_id from phone_call_start or phone_wait_incoming.
        text: What to say. At most 20000 bytes per call.
        wait: Block until the speech is estimated to be finished.
        barge_in: Stop your speech when they talk over you (default true).
    """
    _check_call_id(call_id)
    _check_text(text)
    try:
        session = _session_for_turn(call_id)
        if session.ended:
            raise PhoneError("the call has ended", "call_ended", call_ended=True)
        # The wait cap counts from the call, including the wait for the lock
        # and the say requests themselves.
        wait_until = session.clock() + session.config.say_wait_max
        with session.activity():
            async with session.lock:
                spoken = await session.say(text, bool(barge_in))
                result = {"queued": spoken["queued"], "estimated_seconds": spoken["estimated_seconds"]}
                if wait:
                    # Clamped for clarity; wait_spoken treats a negative wait as 0.
                    remaining = max(0.0, wait_until - session.clock())
                    result["still_speaking"] = await session.wait_spoken(remaining)
                if session.consume_barge_in():
                    result["barge_in"] = True
                result.update(session.status_fields())
    except PhoneError as exc:
        return _error(exc)
    return format_response(result)


@mcp.tool()
async def phone_listen(call_id: str, timeout_seconds: int = 30, end_silence_ms: int = 800) -> str:
    """Listen for the other party's next turn in a live phone conversation. Speech recognition is billed.

    Returns speech already buffered first, otherwise waits for them to speak
    and returns once they stay silent for end_silence_ms.

    Returns {"heard", "barge_in", "timed_out", "truncated", "call_ended",
    "during_agent_speech", "events_connected"}, plus stt_silent_seconds when
    no speech has been recognised for a long time. truncated means they were
    still talking at the time limit; the rest comes with the next listen.

    Args:
        call_id: The call_id from phone_call_start or phone_wait_incoming.
        timeout_seconds: How long to wait for speech, 1 to 120 (default 30).
        end_silence_ms: Silence that ends their turn, 300 to 3000 (default 800).
    """
    _check_call_id(call_id)
    _check_range("timeout_seconds", timeout_seconds, 1, LISTEN_TIMEOUT_MAX)
    _check_range("end_silence_ms", end_silence_ms, *END_SILENCE_MS_RANGE)
    try:
        session = _session_for_turn(call_id)
        if session.ended:
            return format_response(_ended_result(session))
        # Count the timeout from the call, including any wait for the lock.
        started = session.clock()
        with session.activity():
            async with session.lock:
                result = await session.listen(float(timeout_seconds), end_silence_ms / 1000.0, base=started)
                result["barge_in"] = session.consume_barge_in()
                result.update(session.status_fields())
    except PhoneError as exc:
        return _error(exc)
    return format_response(result)


@mcp.tool()
async def phone_hangup(call_id: str) -> str:
    """End a live phone conversation: stop speech and hang up the call, which ends billing.

    Works at any time, including while another phone tool is waiting on the
    same call.

    Args:
        call_id: The call_id from phone_call_start or phone_wait_incoming.
    """
    _check_call_id(call_id)
    try:
        session = _manager().get_session(call_id)
    except PhoneError as exc:
        return _error(exc)
    if session.ended:
        return format_response(
            {"call_id": call_id, "status": "ended", "ended_reason": session.ended_reason}
        )
    await session.hangup("agent_hangup")
    return format_response({"call_id": call_id, "status": "ended"})


@mcp.tool()
async def phone_status(call_id: str = "") -> str:
    """Show the state of live phone conversations in this MCP server (the calls are billed while active).

    With call_id, summarises that call; without it, every call this process
    holds. Each summary has state, elapsed_seconds, remaining_seconds,
    buffered_transcripts and events_connected.

    Args:
        call_id: Optional call_id; empty lists every call.
    """
    manager = _manager()
    try:
        if call_id:
            session = manager.get_session(call_id)
            session.touch()
            return format_response(session.summary())
    except PhoneError as exc:
        return _error(exc)
    return format_response(
        {
            "sessions": [s.summary() for s in manager.sessions.values()],
            "waiting_numbers": sorted(manager.waiters),
            "events_connected": manager.events_connected(),
        }
    )
