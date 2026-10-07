"""PhoneSession: one live call, its speech-to-text and text-to-speech.

State: ``pending -> dialing -> answered -> ended`` for outgoing calls and
``ringing -> answered -> ended`` for incoming ones.

Event callbacks (``on_call_event``, ``on_transcript``, ``on_interim``) run
synchronously from the EventHub receive loop: they never await HTTP. Anything
that needs the network (stopping speech on barge-in or hangup) is spawned as a
background task whose cleanup runs under ``anyio.move_on_after(5,
shield=True)``.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
from typing import Callable

import anyio

from voipbin_mcp.client import VoIPbinAPIError
from voipbin_mcp.phone import Notifier, PhoneConfig, PhoneError, wait_task_done
from voipbin_mcp.phone.events import CallEvent, InterimEvent, TranscriptEvent
from voipbin_mcp.phone.text import split_text

logger = logging.getLogger(__name__)

TERMINAL_CALL_STATUSES = ("hangup",)


async def api_post(client, path: str, body: dict | None = None) -> dict:
    """POST that tolerates an empty success body.

    Some endpoints (``/calls/{id}/talk``) answer 200 with no body, which the
    shared client would fail to decode after the request already succeeded.
    """
    try:
        return await client.post(path, json=body)
    except json.JSONDecodeError:
        return {}


def is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, VoIPbinAPIError) and exc.status_code == 404


def looks_already_active(exc: BaseException) -> bool:
    """Speaking creation refused because an earlier session is still active.

    bin-tts-manager/pkg/speakinghandler/speaking.go:38-54 refuses a second
    active/initiating speaking on the same reference. The error crosses an
    RPC hop, so it is recognised by its message when present and otherwise by
    the conflict/internal status it surfaces as.
    """
    if not isinstance(exc, VoIPbinAPIError):
        return False
    if "already" in (exc.message or "").lower():
        return True
    return exc.status_code in (409, 500)


class PhoneSession:
    def __init__(
        self,
        client,
        *,
        direction: str,
        config: PhoneConfig | None = None,
        language: str = "en-US",
        voice_id: str = "",
        max_duration_seconds: int = 3600,
        number_id: str = "",
        events_connected: Callable[[], bool] = lambda: True,
        on_transcribe: Callable[["PhoneSession", str], None] | None = None,
        on_end: Callable[["PhoneSession"], None] | None = None,
    ):
        self.client = client
        self.config = config or PhoneConfig()
        self.clock = self.config.clock
        now = self.clock()

        self.state = "pending"
        self.call_id = ""
        self.groupcall_id = ""
        self.direction = direction
        self.language = language
        self.voice_id = voice_id
        self.number_id = number_id
        self.caller: dict | None = None
        self.max_duration_seconds = max_duration_seconds

        self.transcribe_id = ""
        self.speaking_id: str | None = None
        self.transcripts: collections.deque = collections.deque()
        self._seen_transcripts: set[str] = set()
        self.last_interim_at: float | None = None
        self.last_transcript_at: float | None = None
        self.last_stt_event_at: float | None = None

        self.say_at = 0.0
        self.speaking_until = 0.0
        self.greeting_until = 0.0
        self.barge_in = True
        self.barge_gen = 0
        self.pending_stop: asyncio.Task | None = None
        self.stale_speaking_ids: list[str] = []
        self.unreported_barge_in = False

        self.ended_reason = ""
        self.hangup_by = ""
        self.ended_at: float | None = None
        self.created_at = now
        self.answered_at: float | None = None
        self.deadline: float | None = None
        self.last_tool_at = now
        self.active_ops = 0
        # Bookkeeping owned by the SessionManager.
        self.slot_held = False
        self.hangup_requested = False
        self.candidate_call_ids: list[str] = []

        self.lock = asyncio.Lock()
        self.changed = Notifier()
        self._events_connected = events_connected
        self._on_transcribe = on_transcribe
        self._on_end = on_end
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ state

    @property
    def ended(self) -> bool:
        return self.state == "ended"

    def touch(self) -> None:
        self.last_tool_at = self.clock()

    @contextlib.contextmanager
    def activity(self):
        """A tool is working on this session: the idle watchdog must wait."""
        self.active_ops += 1
        self.touch()
        try:
            yield
        finally:
            self.active_ops -= 1
            self.touch()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def mark_answered(self) -> None:
        if self.state in ("pending", "dialing", "ringing"):
            now = self.clock()
            self.state = "answered"
            self.answered_at = now
            self.deadline = now + self.max_duration_seconds
            self.changed.notify()

    def mark_ended(self, reason: str, hangup_by: str = "") -> None:
        if self.ended:
            return
        self.state = "ended"
        self.ended_reason = reason or "hangup"
        self.hangup_by = hangup_by
        self.ended_at = self.clock()
        self.speaking_until = min(self.speaking_until, self.ended_at)
        self.changed.notify()
        # The platform does not stop a speaking session on hangup, so do it.
        ids = self._take_speaking_ids()
        if ids:
            self._spawn(self._stop_all(ids))
        if self._on_end is not None:
            self._on_end(self)

    def _take_speaking_ids(self) -> list[str]:
        ids = list(self.stale_speaking_ids)
        if self.speaking_id and self.speaking_id not in ids:
            ids.append(self.speaking_id)
        self.speaking_id = None
        return ids

    # ----------------------------------------------------------------- events

    def on_call_event(self, event: CallEvent) -> None:
        if event.status == "progressing":
            self.mark_answered()
        elif event.status in TERMINAL_CALL_STATUSES:
            self.mark_ended(event.hangup_reason or "hangup", event.hangup_by)

    def on_transcript(self, event: TranscriptEvent) -> None:
        if event.id and event.id in self._seen_transcripts:
            return
        if event.id:
            self._seen_transcripts.add(event.id)
        now = self.clock()
        self.last_transcript_at = now
        self.last_stt_event_at = now
        message = (event.message or "").strip()
        if message:
            self.transcripts.append(
                {
                    "id": event.id,
                    "message": message,
                    "at": now,
                    "during_agent_speech": now < max(self.speaking_until, self.greeting_until),
                }
            )
        self.changed.notify()

    def on_interim(self, event: InterimEvent) -> None:
        now = self.clock()
        self.last_interim_at = now
        self.last_stt_event_at = now
        self._check_barge_in(event.message or "", now)
        self.changed.notify()

    def _check_barge_in(self, message: str, now: float) -> None:
        if not self.barge_in or self.state != "answered" or self.speaking_id is None:
            return
        text = message.strip()
        if not (len(text.split()) >= 2 or len(text) >= 4):
            return
        # Audio reaches the callee 0.9-1.3 s after say; an interim before that
        # is speech that started before the person heard the agent. The
        # greeting played by /talk is not in this window (it cannot be stopped).
        if not (self.say_at + self.config.barge_window_delay <= now <= self.speaking_until):
            return
        old_id = self.speaking_id
        self.speaking_id = None
        self.speaking_until = now
        self.barge_gen += 1
        self.unreported_barge_in = True
        if old_id not in self.stale_speaking_ids:
            self.stale_speaking_ids.append(old_id)
        self.pending_stop = self._spawn(self._stop_speaking(old_id))

    def consume_barge_in(self) -> bool:
        value = self.unreported_barge_in
        self.unreported_barge_in = False
        return value

    # -------------------------------------------------------------- speaking

    async def _stop_speaking(self, speaking_id: str) -> bool:
        stopped = False
        with anyio.move_on_after(5, shield=True):
            try:
                await api_post(self.client, f"/speakings/{speaking_id}/stop")
                stopped = True
            except VoIPbinAPIError as exc:
                stopped = exc.status_code == 404
                logger.info("speaking stop %s failed: %s", speaking_id, exc)
            except Exception as exc:  # pragma: no cover - transport failure
                logger.info("speaking stop %s failed: %s", speaking_id, exc)
        if stopped and speaking_id in self.stale_speaking_ids:
            self.stale_speaking_ids.remove(speaking_id)
        return stopped

    async def _stop_all(self, ids: list[str]) -> None:
        with anyio.move_on_after(5, shield=True):
            for speaking_id in ids:
                if speaking_id not in self.stale_speaking_ids:
                    self.stale_speaking_ids.append(speaking_id)
                await self._stop_speaking(speaking_id)

    async def _post_speaking(self) -> dict:
        body = {
            "reference_type": "call",
            "reference_id": self.call_id,
            "language": self.language,
            "direction": "out",
        }
        if self.voice_id:
            body["voice_id"] = self.voice_id
        return await api_post(self.client, "/speakings", body)

    async def _create_speaking(self, gen: int) -> None:
        try:
            created = await self._post_speaking()
        except VoIPbinAPIError as exc:
            # Retry only when nothing new happened (same generation) and the
            # refusal is the "an earlier speaking is still active" one, after
            # stopping every speaking whose stop is unconfirmed.
            if not (gen == self.barge_gen and self.stale_speaking_ids and looks_already_active(exc)):
                raise
            for speaking_id in list(self.stale_speaking_ids):
                await self._stop_speaking(speaking_id)
            if gen != self.barge_gen:
                return
            created = await self._post_speaking()
        self.speaking_id = created.get("id") or None

    async def start_media(self) -> None:
        """Start STT (aws, inbound leg) and TTS (outbound leg) after answer."""
        transcribe = await api_post(
            self.client,
            "/transcribes",
            {
                "reference_type": "call",
                "reference_id": self.call_id,
                "language": self.language,
                "direction": "in",
                "provider": "aws",
            },
        )
        self.transcribe_id = str(transcribe.get("id") or "")
        if not self.transcribe_id:
            raise PhoneError("the transcribe response carried no id", "media_start_failed")
        if self._on_transcribe is not None:
            self._on_transcribe(self, self.transcribe_id)
        self.last_stt_event_at = self.clock()

        speaking = await self._post_speaking()
        self.speaking_id = speaking.get("id") or None
        if not self.speaking_id:
            raise PhoneError("the speaking response carried no id", "media_start_failed")

        # Transcripts that arrived before the transcribe id was known were
        # dropped by the router; fetch them once.
        await self.reconcile()

    async def say(self, text: str, barge_in: bool = True) -> dict:
        """Send ``text`` to the speaking session. Caller holds ``self.lock``."""
        self.barge_in = barge_in
        gen = self.barge_gen
        if self.pending_stop is not None:
            await wait_task_done(self.pending_stop)
            self.pending_stop = None
        if self.ended:
            raise PhoneError("the call has ended", "call_ended", call_ended=True)
        interrupted = False
        sent = 0
        pieces = split_text(text)
        if gen == self.barge_gen and self.speaking_id is None:
            await self._create_speaking(gen)
        for piece in pieces:
            if gen != self.barge_gen or self.ended or self.speaking_id is None:
                interrupted = True
                break
            now = self.clock()
            if self.speaking_until <= now:
                self.say_at = now
            await api_post(self.client, f"/speakings/{self.speaking_id}/say", {"text": piece})
            if gen != self.barge_gen:
                # The callee spoke while this piece was in flight: no retry,
                # no further pieces.
                interrupted = True
                break
            now = self.clock()
            self.speaking_until = max(now, self.speaking_until) + self.config.estimate_seconds(piece)
            sent += 1
        now = self.clock()
        return {
            "queued": sent > 0,
            "pieces": len(pieces),
            "sent_pieces": sent,
            "estimated_seconds": round(max(0.0, self.speaking_until - now), 1),
            "interrupted": interrupted,
        }

    async def wait_spoken(self, max_wait: float) -> bool:
        """Wait until the estimated playback ends or a barge-in; True = still speaking."""
        start = self.clock()
        gen = self.barge_gen
        while True:
            now = self.clock()
            if gen != self.barge_gen or self.ended or now >= self.speaking_until:
                return False
            if now - start >= max_wait:
                return True
            await self.changed.wait(min(self.speaking_until, start + max_wait) - now)

    # -------------------------------------------------------------- listening

    def drain(self) -> list[dict]:
        items = list(self.transcripts)
        self.transcripts.clear()
        return items

    def _interim_active(self, now: float) -> bool:
        if self.last_interim_at is None:
            return False
        if self.last_transcript_at is not None and self.last_transcript_at >= self.last_interim_at:
            return False
        return now - self.last_interim_at < self.config.interim_active_seconds

    def stt_silent_seconds(self) -> float | None:
        if self.state != "answered" or self.last_stt_event_at is None:
            return None
        silent = self.clock() - self.last_stt_event_at
        if silent >= self.config.stt_silent_warn_seconds:
            return round(silent, 1)
        return None

    async def listen(
        self,
        timeout: float,
        end_silence: float,
        *,
        base: float | None = None,
        hard_end: float | None = None,
    ) -> dict:
        """Collect one turn of the callee's speech. Caller holds ``self.lock``."""
        now = self.clock()
        base = now if base is None else base
        soft = base + timeout
        hard = soft + self.config.listen_grace_seconds
        if hard_end is not None:
            hard = min(hard, hard_end)
            soft = min(soft, hard)
        heard: list[dict] = []
        timed_out = False
        truncated = False
        reconciled = False
        while True:
            heard.extend(self.drain())
            now = self.clock()
            if self.ended:
                break
            if heard:
                last = max(t for t in (self.last_transcript_at, self.last_interim_at) if t is not None)
                if now - last >= end_silence:
                    break
                if now >= hard:
                    truncated = True
                    break
                wake = min(last + end_silence, hard)
            else:
                interim_active = self._interim_active(now)
                if now >= hard or (now >= soft and not interim_active):
                    if not reconciled:
                        # A full subscriber buffer drops events silently;
                        # fetch what may have been missed before giving up.
                        reconciled = True
                        await self.reconcile()
                        continue
                    timed_out = True
                    truncated = interim_active
                    break
                if now >= soft:
                    last_interim = self.last_interim_at if self.last_interim_at is not None else now
                    wake = min(last_interim + self.config.interim_active_seconds, hard)
                else:
                    wake = soft
            await self.changed.wait(max(0.0, wake - now))
        return {
            "heard": " ".join(item["message"] for item in heard),
            "timed_out": timed_out and not heard,
            "truncated": truncated,
            "call_ended": self.ended,
            "during_agent_speech": any(item["during_agent_speech"] for item in heard),
        }

    def status_fields(self) -> dict:
        data: dict = {"events_connected": self._events_connected()}
        silent = self.stt_silent_seconds()
        if silent is not None:
            data["stt_silent_seconds"] = silent
        if self.ended:
            data["ended_reason"] = self.ended_reason
        return data

    # -------------------------------------------------------------- reconcile

    async def reconcile(self) -> None:
        """Correct call state and missed transcripts from the REST API."""
        if self.call_id and not self.ended:
            try:
                call = await self.client.get(f"/calls/{self.call_id}")
                status = call.get("status")
                if status == "progressing":
                    self.mark_answered()
                elif status in TERMINAL_CALL_STATUSES:
                    self.mark_ended(call.get("hangup_reason") or "hangup", call.get("hangup_by") or "")
            except Exception as exc:
                logger.info("reconcile of call %s failed: %s", self.call_id, exc)
        if not self.transcribe_id:
            return
        items: list[dict] = []
        token = ""
        try:
            for _ in range(100):
                params = {"transcribe_id": self.transcribe_id, "page_size": 100}
                if token:
                    params["page_token"] = token
                page = await self.client.get("/transcripts", params=params)
                items.extend(page.get("result") or [])
                next_token = page.get("next_page_token") or ""
                if not next_token or next_token == token or not page.get("result"):
                    break
                token = next_token
        except Exception as exc:
            logger.info("reconcile of transcripts %s failed: %s", self.transcribe_id, exc)
        items.sort(key=lambda item: (str(item.get("tm_create") or ""), int(item.get("offset_ms") or 0)))
        for item in items:
            if item.get("direction") not in (None, "", "in"):
                continue
            self.on_transcript(
                TranscriptEvent(
                    id=str(item.get("id") or ""),
                    transcribe_id=self.transcribe_id,
                    direction=str(item.get("direction") or ""),
                    message=str(item.get("message") or ""),
                )
            )

    # ----------------------------------------------------------------- hangup

    async def hangup(self, reason: str = "agent_hangup") -> None:
        """Stop speech and hang up; failures are logged, never raised."""
        ids = self._take_speaking_ids()
        call_id = self.call_id
        self.mark_ended(reason)
        with anyio.move_on_after(5, shield=True):
            for speaking_id in ids:
                if speaking_id not in self.stale_speaking_ids:
                    self.stale_speaking_ids.append(speaking_id)
                await self._stop_speaking(speaking_id)
            if call_id:
                try:
                    await api_post(self.client, f"/calls/{call_id}/hangup")
                except Exception as exc:
                    # 404 or already ended is fine.
                    logger.info("hangup of call %s: %s", call_id, exc)

    def summary(self) -> dict:
        now = self.clock()
        start = self.answered_at or self.created_at
        data = {
            "call_id": self.call_id,
            "state": self.state,
            "direction": self.direction,
            "elapsed_seconds": round((self.ended_at or now) - start, 1),
            "buffered_transcripts": len(self.transcripts),
        }
        if self.deadline is not None and not self.ended:
            data["remaining_seconds"] = round(max(0.0, self.deadline - now), 1)
        if self.caller:
            data["caller"] = self.caller
        data.update(self.status_fields())
        return data
