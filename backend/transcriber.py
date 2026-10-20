"""
PitchMirror — dedicated live speech-to-text.

The coaching model's own input transcription is a by-product of a conversational
audio model: it guesses the language per word, drops words and stalls while the
model is generating. A dedicated transcription model (Gemini Live
`gemini-3.5-transcribe-live`) is far more accurate (including Hinglish) and has no
daily request cap on the free tier, so every audio stream that needs a transcript —
the presenter's mic and, in meeting mode, the other participant's audio — gets its
own LiveTranscriber.

Each finished segment (the model emits one after a short pause) is delivered to
`on_text(text)`; the caller stores it and forwards it to the browser.
"""
from __future__ import annotations

import array
import asyncio
from collections import deque
import logging
import math
import os
import time
from typing import Callable

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

TRANSCRIBE_MODEL = os.getenv("PITCHMIRROR_TRANSCRIBE_MODEL", "gemini-3.5-transcribe-live").strip()
_QUEUE_MAX_CHUNKS = 300          # ~30 s of 100 ms chunks buffered while reconnecting
_RECONNECT_DELAYS_S = (1, 2, 4, 8, 15)


def transcription_enabled() -> bool:
    return bool(TRANSCRIBE_MODEL) and bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))


# Live transcription sessions are capped (~10 min). Hand over to a fresh session before
# that, preferably during a pause so no sentence is split across sessions.
ROLLOVER_AFTER_S = float(os.getenv("PITCHMIRROR_STT_ROLLOVER_S", "510"))   # 8.5 min: look for a pause
ROLLOVER_FORCE_S = float(os.getenv("PITCHMIRROR_STT_ROLLOVER_FORCE_S", "570"))  # 9.5 min: hand over anyway
# Hand over right after a segment came back (little unconfirmed audio) and during a pause.
ROLLOVER_PAUSE_S = 0.8
ROLLOVER_MAX_UNCONFIRMED_S = 4.0
# Watchdog: plenty of voice reached the transcriber but no text came back → reconnect.
# The model only returns text after a ~2 s pause, so a long monologue alone mustn't trip it.
STALL_VOICED_S = 40.0
STALL_NO_TEXT_S = 60.0
_VOICE_RMS_INT16 = 400          # chunk RMS (16-bit) that counts as speech after levelling
_CHUNK_S = 0.1
# Audio sent since the last returned segment is kept so a handover can replay it into the
# new session (nothing lost, nothing duplicated). Includes a little audio before the last
# segment arrived, which may belong to the next utterance.
_UNCONFIRMED_MAX_CHUNKS = 300   # 30 s
_UNCONFIRMED_LEAD_S = 1.0
_REPLAY_PACE_S = 0.025          # replay at ~4x real time


class LiveTranscriber:
    """
    Streams 16 kHz PCM to a live transcription session.

    Reconnects on drops, rolls over to a new session before the session cap (at a pause
    when possible), and restarts a session that hears speech but returns no text.
    Audio is buffered while (re)connecting, so nothing said during a handover is lost.
    """

    def __init__(
        self,
        name: str,
        on_text: Callable[[str], None],
        *,
        session_id: str = "",
        on_status: Callable[[str], None] | None = None,
    ):
        self.name = name
        self.on_text = on_text
        self.on_status = on_status
        self.session_id = session_id
        self.connected = False
        self.failed = False          # gave up — caller may fall back to another transcript source
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_QUEUE_MAX_CHUNKS)
        self._closed = False
        self._replay: list[bytes] = []       # unconfirmed audio to resend after a handover
        self._unconfirmed: deque[tuple[float, bytes]] = deque(maxlen=_UNCONFIRMED_MAX_CHUNKS)
        self._last_voice_at = 0.0
        self._last_text_at = 0.0
        self._voiced_since_text = 0.0
        self.stats = {"segments": 0, "connects": 0, "rollovers": 0, "stalls": 0, "errors": 0}

    @staticmethod
    def _rms(pcm: bytes) -> float:
        samples = array.array("h", pcm[: len(pcm) - (len(pcm) % 2)])
        if not samples:
            return 0.0
        return math.sqrt(sum(x * x for x in samples) / len(samples))

    def push(self, pcm: bytes) -> None:
        """Queue one PCM chunk (non-blocking; drops the oldest audio if the buffer is full)."""
        if self._closed or self.failed:
            return
        if self._rms(pcm) >= _VOICE_RMS_INT16:
            self._last_voice_at = time.monotonic()
            self._voiced_since_text += _CHUNK_S
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(pcm)

    def close(self) -> None:
        self._closed = True
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    def _status(self, state: str) -> None:
        if self.on_status:
            try:
                self.on_status(state)
            except Exception:
                logger.exception("transcriber_status_callback_failed stream=%s", self.name)

    async def run(self) -> None:
        """Run until close(); reconnects on drops, rollovers and stalls."""
        client = genai.Client()
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.TEXT],
            input_audio_transcription=types.AudioTranscriptionConfig(),
        )
        attempt = 0
        while not self._closed:
            try:
                async with client.aio.live.connect(model=TRANSCRIBE_MODEL, config=config) as session:
                    self.connected = True
                    self.stats["connects"] += 1
                    attempt = 0
                    started = time.monotonic()
                    self._last_text_at = started
                    self._voiced_since_text = 0.0
                    logger.info("transcriber_connected session=%s stream=%s", self.session_id, self.name)
                    if self.stats["connects"] > 1 and not self.failed:
                        self._status("ok")
                    sender = asyncio.create_task(self._send_loop(session, started))
                    receiver = asyncio.create_task(self._receive_loop(session))
                    try:
                        done, _ = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
                        if sender in done:
                            reason = sender.result()   # re-raises send errors → reconnect
                            if not reason:
                                # Closing: give the model a moment to emit the last segment.
                                await asyncio.wait({receiver}, timeout=4.0)
                            # Rollover / stall: the unconfirmed audio is replayed into the
                            # next session, so the old one is dropped right away.
                        else:
                            receiver.result()          # server ended the connection → reconnect
                            self._stage_replay()
                    finally:
                        for task in (sender, receiver):
                            task.cancel()
                        self.connected = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:   # network drop, session limit, model error
                self.connected = False
                self.stats["errors"] += 1
                self._stage_replay()
                if self._closed:
                    break
                delay = _RECONNECT_DELAYS_S[min(attempt, len(_RECONNECT_DELAYS_S) - 1)]
                attempt += 1
                logger.warning(
                    "transcriber_error session=%s stream=%s attempt=%d retry_in=%ss err=%s",
                    self.session_id, self.name, attempt, delay, str(exc)[:200],
                )
                if attempt > len(_RECONNECT_DELAYS_S) * 2:
                    self.failed = True
                    self._status("failed")
                    logger.error("transcriber_gave_up session=%s stream=%s", self.session_id, self.name)
                    return
                self._status("recovering")
                await asyncio.sleep(delay)
        logger.info("transcriber_closed session=%s stream=%s stats=%s", self.session_id, self.name, self.stats)

    def _handover_reason(self, started: float) -> str | None:
        now = time.monotonic()
        age = now - started
        unconfirmed_s = len(self._unconfirmed) * _CHUNK_S
        if age >= ROLLOVER_FORCE_S or (
            age >= ROLLOVER_AFTER_S
            and now - self._last_voice_at >= ROLLOVER_PAUSE_S
            and unconfirmed_s <= ROLLOVER_MAX_UNCONFIRMED_S
        ):
            return "rollover"
        if (
            age >= STALL_NO_TEXT_S
            and self._voiced_since_text >= STALL_VOICED_S
            and now - self._last_text_at >= STALL_NO_TEXT_S
        ):
            return "stall"
        return None

    def _stage_replay(self, extra: bytes | None = None) -> None:
        """Queue the audio the old session never confirmed for the next session."""
        chunks = [c for _, c in self._unconfirmed]
        if extra is not None:
            chunks.append(extra)
        self._unconfirmed.clear()
        self._replay = (chunks + self._replay)[-_UNCONFIRMED_MAX_CHUNKS:]

    async def _send(self, session, chunk: bytes) -> None:
        await session.send_realtime_input(audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000"))
        self._unconfirmed.append((time.monotonic(), chunk))

    async def _send_loop(self, session, started: float) -> str | None:
        """Send audio until closed (None) or a handover is due ("rollover" / "stall")."""
        if self._replay:
            replay, self._replay = self._replay, []
            logger.info("transcriber_replay session=%s stream=%s seconds=%.1f",
                        self.session_id, self.name, len(replay) * _CHUNK_S)
            for chunk in replay:
                await self._send(session, chunk)
                await asyncio.sleep(_REPLAY_PACE_S)
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                await self._flush(session, 10)
                return None
            reason = self._handover_reason(started)
            if reason:
                self._stage_replay(extra=chunk)
                self.stats["rollovers" if reason == "rollover" else "stalls"] += 1
                logger.info(
                    "transcriber_%s session=%s stream=%s age=%.0fs voiced_since_text=%.1fs replay=%.1fs",
                    reason, self.session_id, self.name, time.monotonic() - started,
                    self._voiced_since_text, len(self._replay) * _CHUNK_S,
                )
                if reason == "stall":
                    self._status("recovering")
                return reason
            await self._send(session, chunk)

    @staticmethod
    async def _flush(session, chunks: int) -> None:
        """A little silence lets the model finish (and emit) the current segment."""
        for _ in range(chunks):
            await session.send_realtime_input(audio=types.Blob(data=b"\0" * 3200, mime_type="audio/pcm;rate=16000"))

    async def _receive_loop(self, session) -> None:
        # session.receive() stops after each model turn, so keep re-entering it; an
        # empty pass means the connection is gone.
        while True:
            got_any = False
            async for msg in session.receive():
                got_any = True
                sc = msg.server_content
                if sc and sc.input_transcription and sc.input_transcription.text:
                    text = " ".join(sc.input_transcription.text.split())
                    if text:
                        self._last_text_at = time.monotonic()
                        self._voiced_since_text = 0.0
                        # Audio up to shortly before this segment is now confirmed.
                        cutoff = self._last_text_at - _UNCONFIRMED_LEAD_S
                        while self._unconfirmed and self._unconfirmed[0][0] < cutoff:
                            self._unconfirmed.popleft()
                        self.stats["segments"] += 1
                        try:
                            self.on_text(text)
                        except Exception:   # never let a UI/storage error kill transcription
                            logger.exception("transcriber_callback_failed stream=%s", self.name)
            if not got_any:
                return
