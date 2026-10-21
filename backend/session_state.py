"""
Pitchlight — per-session state container.

Shared between the ADK tool closures, the WebSocket handler, and the scorecard builder.
Tool functions write into this object synchronously (put_nowait); the WebSocket sender
drains ws_event_queue asynchronously via a sentinel-driven loop.
"""
import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Rolling transcript cap — prevents unbounded context growth in long sessions.
# Live transcription arrives as few-word fragments; add_transcript merges them into
# segments of up to _SEGMENT_MAX_SPAN_S, so 400 segments covers well over 30 minutes.
MAX_TRANSCRIPT_SEGMENTS = 1200   # ~2.5 h of speech; meetings run long
_SEGMENT_MAX_SPAN_S = 8.0   # keep segments short so rolling WPM/filler windows stay accurate
_SEGMENT_MAX_GAP_S = 3.0    # a pause longer than this starts a new segment
MAX_TIMELINE_EVENTS = 500
_PROSODY_WINDOW_S = 20.0

# Unambiguous spoken fillers, counted straight from the presenter transcript for
# scoring. Narrower than the live tool regex ("so", "like", "right" are often
# legitimate words) so the scorecard doesn't over-penalise.
_SCORING_FILLER_RE = re.compile(
    r"\b(um+|uh+|erm+|uhm+|you know|basically|literally|kind of|sort of|i mean)\b",
    re.IGNORECASE,
)

# Stumbles: the same word or short phrase said twice in a row ("it it", "Chief OS Chief OS").
# Words that are normally doubled are not stumbles.
_STUMBLE_MAX_PHRASE = 6
_STUMBLE_OK_DOUBLES = frozenset({
    "very", "really", "so", "bye", "ha", "haha", "hey", "no", "yes", "yeah", "okay", "ok", "well",
    "um", "uh", "erm", "hmm", "mm",   # fillers are counted separately
})
_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")


def count_stumbles(text: str) -> int:
    """Repeated words/phrases in Latin-script speech; each extra copy counts once."""
    words = _WORD_RE.findall((text or "").lower())
    count, i = 0, 0
    while i < len(words):
        for n in range(min(_STUMBLE_MAX_PHRASE, (len(words) - i) // 2), 0, -1):
            if words[i:i + n] == words[i + n:i + 2 * n] and not (n == 1 and words[i] in _STUMBLE_OK_DOUBLES):
                count += 1
                i += n          # a triple ("it it it") counts twice
                break
        else:
            i += 1
    return count


# Setup chatter: what people say to the room while starting or stopping a session
# ("start bolna start listen", "ok it's on", "हो गया … खत्म कर"). Only trimmed near the edges.
CHATTER_EDGE_S = 20.0
_CHATTER_RE = re.compile(
    r"^(?:start|started|starting|stop|stopped|test|testing|listen|listening|recording|record|on|off|"
    r"ok|okay|ready|done|go|begin|now|it|is|it's|its|this|working|can|you|hear|me|let's|lets|"
    r"bolna|bol|chalo|chal|bas|ho|gaya|hogaya|khatam|khatm|shuru|kar|karo|haan|ha|acha|accha)$",
    re.IGNORECASE,
)
_CHUNK_RE = re.compile(r"[A-Za-z0-9'’]+|[^\sA-Za-z0-9'’]+")


def _chunk_script(chunk: str) -> str:
    """'latin' | 'other' (a letter from another script) | 'neutral' (punctuation, digits)."""
    if re.search(r"[A-Za-z]", chunk):
        return "latin"
    if any(ch.isalpha() for ch in chunk):
        return "other"
    return "neutral"


def dominant_script(texts: list[str]) -> str | None:
    """'latin' or 'other' when one script holds at least 60% of the letters, else None."""
    latin = other = 0
    for t in texts:
        for ch in t:
            if ch.isalpha():
                if ch.isascii():
                    latin += 1
                else:
                    other += 1
    total = latin + other
    if not total:
        return None
    if latin / total >= 0.6:
        return "latin"
    if other / total >= 0.6:
        return "other"
    return None


def trim_edge_text(text: str, script: str | None) -> str:
    """Drop leading/trailing runs written in the non-dominant script (asides around the pitch)."""
    chunks = _CHUNK_RE.findall(text or "")
    if not script or not chunks:
        return (text or "").strip()
    keep = [i for i, c in enumerate(chunks) if _chunk_script(c) == script]
    if not keep:
        return ""
    start, end = keep[0], keep[-1]
    # Re-join the kept span from the original text so spacing/punctuation stay as spoken.
    first, last = chunks[start], chunks[end]
    lo = (text or "").find(first)
    hi = (text or "").rfind(last) + len(last)
    span = (text or "")[lo:hi] if lo != -1 and hi > lo else " ".join(chunks[start:end + 1])
    # A trailing sentence-ending mark right after the span belongs to it.
    tail = (text or "")[hi:hi + 1]
    return (span + (tail if tail in ".!?" else "")).strip()


def is_setup_chatter(text: str) -> bool:
    """Short line made mostly of start/stop/test words ("start bolna start listen", "ok it's on")."""
    words = re.findall(r"[A-Za-z']+", text or "")
    if not words or len(words) > 6:
        return False
    return sum(1 for w in words if _CHATTER_RE.match(w)) / len(words) >= 0.6


# Sentinel value: putting this into ws_event_queue signals the drain task to exit.
_DRAIN_STOP = None

# Coach speech that is actually a leaked tool call / code ("tool_code m_1 = flag_issue(...)").
# The native-audio model occasionally verbalises its tool plan instead of calling the tool.
_TOOL_TEXT_RE = re.compile(
    r"tool_code|tool_call|default_api|\b(flag_issue|check_eye_contact|get_speech_metrics|"
    r"draw_overlay|get_recent_transcript|check_slide_clarity|navigate_practice_slides|"
    r"jump_to_slide|mark_slide_issue|generate_live_visual_hint)\b|\w+\s*=\s*\w+\(",
    re.IGNORECASE,
)

# Coach speech attaches to the most recent coaching event if it arrives within this window.
_COACH_SAID_WINDOW_S = 20.0


_QUESTION_START_RE = re.compile(
    r"^\s*(what|why|how|when|where|which|who|whom|whose|can you|could you|would you|will you|do you|did you|"
    r"are you|have you|is there|tell me|walk me|talk me|describe|explain|give me|share|"
    r"what's|how's|why's)\b",
    re.IGNORECASE,
)


def looks_like_question(text: str) -> bool:
    """Heuristic: is this utterance (from the other participant) a question to answer?"""
    t = (text or "").strip()
    if len(t.split()) < 4:
        return False
    if "?" in t:
        return True
    # Spoken questions often lose the "?" in transcription; check the last sentence's opening.
    last = re.split(r"(?<=[.!])\s+", t)[-1]
    return bool(_QUESTION_START_RE.match(last))


def extract_qa_pairs(transcript: list[dict], *, max_pairs: int = 12) -> list[dict]:
    """
    Pair each question asked by the other participant ("o") with the presenter's ("u")
    answer that follows it, until the next question.
    """
    pairs: list[dict] = []
    current: dict | None = None
    for seg in transcript:
        speaker, text = seg.get("s"), (seg.get("t") or "").strip()
        if not text:
            continue
        if speaker == "o" and looks_like_question(text):
            if current and current["answer"].strip():
                pairs.append(current)
            current = {"question": text, "ts": seg.get("ts", 0), "answer": ""}
        elif speaker == "o" and current and not current["answer"]:
            current["question"] = f"{current['question']} {text}"   # question spoken across segments
        elif speaker == "u" and current is not None:
            current["answer"] = f"{current['answer']} {text}".strip()
    if current and current["answer"].strip():
        pairs.append(current)
    for p in pairs:
        p["question"] = p["question"][-400:]
        p["answer"] = p["answer"][:1500]
    return pairs[-max_pairs:]


def looks_like_tool_text(text: str) -> bool:
    return bool(_TOOL_TEXT_RE.search(text or ""))


@dataclass
class CoachingEvent:
    timestamp: float
    event_type: str   # "filler" | "pace" | "eye_contact" | "contradiction" | "clarity" | slide_*
    description: str
    evidence: dict = field(default_factory=dict)  # metric snapshot: threshold + measured value
    coach_said: str = ""    # what the coach actually said out loud for this issue (if anything)
    user_rating: str = ""   # presenter's 👍/👎 on this nudge: "up" | "down" | ""
    source: str = "model"   # "model" (live agent flag_issue) | "sensor" (server-side measurement)


@dataclass
class SessionState:
    session_id: str
    user_id: str = "anon"
    coach_mode: str = "general"
    delivery_context: str = "virtual"
    primary_goal: str = "balanced"
    persona: str = "coach"
    # "practice" (in-app rehearsal) | "meeting" (floating coach over Zoom/Meet/Teams)
    session_kind: str = "practice"
    platform: str = ""
    # Dedicated live transcribers (backend/transcriber.py): presenter mic and, in meeting
    # mode, the other participant's audio. None when transcription is disabled.
    user_stt: object = None
    other_stt: object = None
    other_stt_task: object = None   # started on the first meeting-audio frame
    other_audio_seen: bool = False
    # Meeting-mode content check sent to the live model and awaiting its one-line verdict.
    content_check_sent_at: float = 0.0
    # Live answer helper: question awaiting an outline from the live model, and the hints shown.
    hint_question: str = ""
    hint_sent_at: float = 0.0
    answer_hints: list[dict] = field(default_factory=list)
    # Post-meeting question-by-question review (list of {question, answer, rating, ...}).
    qa_review: list[dict] = field(default_factory=list)
    # Live request queue of the coaching model (set by the WebSocket handler).
    live_queue: object = None
    # Profile/memory update deferred until after the scorecard is delivered.
    pending_profile_update: object = None
    # Pre-call brief (meeting mode): notes, points to cover [{text, covered, ts}],
    # planned length, and whether the end-of-meeting reminder was shown.
    brief_notes: str = ""
    brief_points: list[dict] = field(default_factory=list)
    planned_minutes: int = 0
    brief_reminded: bool = False
    # After-call recap: summary, decisions, action items, follow-up email.
    recap: dict = field(default_factory=dict)
    # Live read of the other participant: [{ts, mood, reason}]
    client_moods: list[dict] = field(default_factory=list)
    interruptions: int = 0
    # Who the meeting is with (pre-call brief) and what was said last time with them.
    client_name: str = ""
    previous_call: dict = field(default_factory=dict)
    # Promises made during the call: [{ts, owner: "you" | "them", task, due}]
    commitments: list[dict] = field(default_factory=list)
    # Voice-activity talk time reported by the browser: {you_s, them_s, you_share, longest_monologue_s}
    talk_stats: dict = field(default_factory=dict)
    # Practice: weakest passages rewritten stronger [{original, rewrite, why, ts}]
    rewrites: list[dict] = field(default_factory=list)
    # Setup chatter removed from the start/end of the presenter's transcript [{ts, text}]
    ignored_chatter: list[dict] = field(default_factory=list)
    # Meeting: every question the other side asked [{ts, text}], so the presenter can get an answer
    # for an earlier one from the "Questions" list in the answers panel.
    client_questions: list[dict] = field(default_factory=list)
    # Meeting: concrete facts said in the call [{ts, kind, text, who}] (names, numbers, dates, needs, tools)
    facts: list[dict] = field(default_factory=list)
    # Practice: named pitch being rehearsed, its key points, and the result {name, version, points}
    pitch_name: str = ""
    pitch_points: list[str] = field(default_factory=list)
    pitch: dict = field(default_factory=dict)
    # Script follow-along (see script_follow.py), the person's own documents (library.py) and the results
    script_id: str = ""
    script_name: str = ""
    script_target_minutes: int = 0
    script_follower: object = None       # ScriptFollower | None
    script_lost_pending: bool = False
    last_client_question_at: float = -1e9   # monotonic time the client last asked something
    hard_questions: list = field(default_factory=list)   # [{question, answer}] the presenter prepared (script / demo kit)
    library: object = None               # Library | None (the presenter's own documents)
    practice_meeting: bool = False       # the built-in practice meeting with the dummy client (demo_kit.py)
    venture: str = ""                    # the company business this call is about (searched first in the hub)
    company_library: object = None       # Library | None (admin-managed company knowledge hub)
    hint_sources: dict = field(default_factory=dict)   # question -> {"sources": [...], "not_in_notes": bool}
    script_result: dict = field(default_factory=dict)
    readiness: dict = field(default_factory=dict)
    # Practice: "" (coach only) or the role the live voice plays: investor | interviewer | client
    roleplay_role: str = ""
    roleplay_started: bool = False
    # Live advice: how chatty the meeting coach is (quiet = flags only | balanced | active), whether
    # practice shows feedback live or only at the end, and what was advised [{ts, kind, text}].
    chattiness: str = "balanced"
    live_feedback: bool = True
    live_advice: list[dict] = field(default_factory=list)
    last_advice_at: float = 0.0          # monotonic
    last_good_at: float = 0.0            # monotonic
    advice_pending: bool = False
    # Meeting: prepared objection answers from the brief [{label, answer}] and objection cards shown.
    prepared_answers: list[dict] = field(default_factory=list)
    objections: list[dict] = field(default_factory=list)
    last_objection_at: float = 0.0       # monotonic
    objection_class_at: dict = field(default_factory=dict)   # kind → monotonic time of its last card
    # Meeting: open questions the presenter asked (question coach) and the next-step guard.
    open_questions: int = 0
    last_open_question_ts: float = 0.0   # seconds into the session
    question_nudged_at: float = -1e9     # seconds into the session
    next_step_checked: bool = False
    # Per-stream transcriber stats (segments, connects, rollovers, stalls, errors).
    transcription_health: dict = field(default_factory=dict)
    screen_enabled: bool = False
    demo_mode: bool = False
    previous_summary: str = ""
    total_slides: int = 0
    current_slide_index: int = -1
    session_start: float = field(default_factory=time.time)

    # Structured coaching events (populated by flag_issue tool calls)
    events: list[CoachingEvent] = field(default_factory=list)
    # Replay-friendly chronological timeline (interruptions, tool actions, slide moves, telemetry).
    timeline_events: list[dict] = field(default_factory=list)

    # Rolling transcript — capped at MAX_TRANSCRIPT_SEGMENTS to bound context size
    transcript: list[dict] = field(default_factory=list)

    # Async queue: tool functions put metric updates here; WebSocket sender drains it.
    # Bounded at 256 to cap memory under backpressure; events dropped when full are
    # non-critical metric/tool-call UI updates — the session continues unaffected.
    # Sentinel None signals the drain task to stop cleanly.
    ws_event_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=256))

    # Diagnostics — count of events dropped due to queue backpressure
    queue_drop_count: int = 0

    # Metric counters (derived from events for fast access)
    filler_count: int = 0
    eye_contact_drops: int = 0
    pace_violations: int = 0
    contradictions: int = 0
    clarity_flags: int = 0
    visual_flags: int = 0
    mismatch_flags: int = 0
    live_visual_hints_ready: int = 0
    # Confirmed disengaged-gaze streaks seen by check_eye_contact, counted even when
    # the resulting flag_issue interruption is suppressed by cooldown gates.
    gaze_away_streaks: int = 0
    # Look-away streaks measured by in-browser face tracking (MediaPipe), the most
    # reliable source since it runs on every frame instead of when the model checks.
    browser_gaze_streaks: int = 0
    browser_gaze: str = "camera"
    _browser_away_since: float = 0.0
    _browser_streak_counted: bool = False

    # Set False when the live session ends
    is_active: bool = True
    # Set when the live phase is over (drain stopped) — background watchers exit on it.
    live_closed: bool = False

    # Server-side hook to the session's flag_issue tool (set by make_coaching_tools),
    # so sensor-confirmed issues go through the same gates/cooldowns as model flags.
    flag_issue_fn: object = None

    # monotonic timestamp of the last coach audio chunk forwarded to the browser.
    # Used by _downstream to enforce a hard speech cooldown server-side.
    last_coach_speech_time: float = 0.0
    # Monotonic timestamp + issue type of the last *confirmed* interruption.
    # _downstream suppresses spontaneous model speech unless this was set recently.
    last_confirmed_issue_time: float = 0.0
    last_confirmed_issue_type: str = ""

    # Last slide/screen frame received as base64 JPEG — used as "before" in
    # generate_live_visual_hint before/after slide redesign comparison.
    last_slide_frame_b64: str = ""

    # Written analysis from post-session pipeline
    final_report: str = ""
    research_tips: str = ""
    generated_assets: list[dict] = field(default_factory=list)
    # Holistic scores (0–100) from synthesis agent; override metric-based scoring when present
    ai_scores: dict = field(default_factory=dict)
    # Rolling speech-signal timestamps for two-signal interruption gating.
    signal_hits: dict[str, float] = field(default_factory=dict)
    _signal_emit_ts: dict[str, float] = field(default_factory=dict)
    # Rolling audio features from the raw PCM path for prosody intelligence.
    prosody_window: deque = field(default_factory=lambda: deque(maxlen=320))
    prosody_metrics: dict = field(default_factory=lambda: {
        "pitch_variance_hz": 0.0,
        "pause_ratio_20s": 0.0,
        "speaking_energy": 0.0,
        "monotony_score": 0.0,
        "voiced_seconds_20s": 0.0,
    })

    def _enqueue(self, event: dict) -> None:
        """Non-blocking enqueue; silently drops when the queue is full.

        The drained events are non-critical UI updates (metrics, tool-call badges).
        Dropping them never affects session logic or scoring.
        """
        try:
            self.ws_event_queue.put_nowait(event)
        except asyncio.QueueFull:
            self.queue_drop_count += 1
            logger.debug(
                "ws_event_queue full — dropping %s event (session=%s, drops=%d)",
                event.get("type"), self.session_id, self.queue_drop_count,
            )

        # Keep a lightweight replay timeline for post-session scrubbing.
        etype = str(event.get("type") or "")
        if etype == "tool_call":
            tool = str(event.get("tool") or "tool")
            self.record_timeline("tool_call", tool, {"tool": tool}, enqueue=False)
        elif etype == "slide_change":
            idx = event.get("current_slide_index")
            self.record_timeline(
                "slide_change",
                f"Slide {int(idx) + 1}" if isinstance(idx, int) else "Slide change",
                {"index": idx, "source": event.get("source")},
                enqueue=False,
            )
        elif etype == "telemetry":
            phase = str(event.get("phase") or "telemetry")
            detail = str(event.get("detail") or "")
            self.record_timeline("telemetry", f"{phase}: {detail}".strip(": "), {"phase": phase}, enqueue=False)
        elif etype == "demo_seed":
            self.record_timeline("demo_seed", str(event.get("message") or "Demo checkpoint"), enqueue=False)

    def record_event(
        self,
        event_type: str,
        description: str,
        evidence: dict | None = None,
        *,
        source: str = "model",
    ) -> None:
        """Called synchronously from ADK tool functions."""
        ts = round(time.time() - self.session_start, 1)
        self.events.append(CoachingEvent(
            timestamp=ts,
            event_type=event_type,
            description=description,
            evidence=evidence or {},
            source=source,
        ))

        if event_type == "filler":
            self.filler_count += 1
        elif event_type == "eye_contact":
            self.eye_contact_drops += 1
        elif event_type == "pace":
            self.pace_violations += 1
        elif event_type == "contradiction":
            self.contradictions += 1
        elif event_type == "clarity":
            self.clarity_flags += 1
        elif event_type == "slide_clarity":
            self.visual_flags += 1
        elif event_type == "slide_mismatch":
            self.mismatch_flags += 1

        self.record_timeline(
            "interruption",
            description,
            {"issue_type": event_type, "evidence": evidence or {}},
            enqueue=False,
        )

        self._enqueue({
            "type": "metric",
            "key": event_type,
            "value": self._count(event_type),
        })
        # One structured card per coaching moment for the live "Coach Suggestions" panel.
        self._enqueue({
            "type": "coach_tip",
            "index": len(self.events) - 1,
            "ts": ts,
            "issue_type": event_type,
            "description": description,
            "evidence": evidence or {},
            "source": source,
        })

    def record_timeline(
        self,
        event_type: str,
        label: str,
        data: dict | None = None,
        *,
        enqueue: bool = False,
    ) -> None:
        row = {
            "ts": round(time.time() - self.session_start, 1),
            "event_type": event_type,
            "label": (label or "")[:220],
            "data": data or {},
        }
        self.timeline_events.append(row)
        if len(self.timeline_events) > MAX_TIMELINE_EVENTS:
            self.timeline_events = self.timeline_events[-MAX_TIMELINE_EVENTS:]
        if enqueue:
            self._enqueue({"type": "timeline", "event": row})

    def record_signal(
        self,
        signal: str,
        *,
        measured: float | int | None = None,
        threshold: str | None = None,
        source: str = "",
    ) -> None:
        now = time.monotonic()
        self.signal_hits[signal] = now
        # Emit signal spikes at most once every 8 seconds per signal to avoid UI spam.
        if now - self._signal_emit_ts.get(signal, 0.0) >= 8.0:
            self._signal_emit_ts[signal] = now
            detail_parts = [signal]
            if measured is not None:
                detail_parts.append(f"measured={measured}")
            if threshold:
                detail_parts.append(f"threshold={threshold}")
            if source:
                detail_parts.append(f"source={source}")
            self.record_timeline("signal_spike", " | ".join(detail_parts), {"signal": signal}, enqueue=False)

    def has_recent_signals(self, signal_names: list[str], *, window_s: float) -> list[str]:
        now = time.monotonic()
        hits: list[str] = []
        for sig in signal_names:
            ts = self.signal_hits.get(sig, 0.0)
            if ts and (now - ts) <= window_s:
                hits.append(sig)
        return hits

    def register_confirmed_issue(self, issue_type: str) -> None:
        self.last_confirmed_issue_time = time.monotonic()
        self.last_confirmed_issue_type = issue_type

    def update_prosody(
        self,
        *,
        rms: float,
        pitch_hz: float | None,
        speech_active: bool,
        chunk_duration_s: float = 0.1,
    ) -> dict:
        now_rel = self.duration_seconds()
        self.prosody_window.append({
            "ts": now_rel,
            "rms": max(0.0, float(rms)),
            "pitch": float(pitch_hz) if pitch_hz else None,
            "speech": bool(speech_active),
            "dur": max(0.02, float(chunk_duration_s)),
        })
        cutoff = now_rel - _PROSODY_WINDOW_S
        while self.prosody_window and self.prosody_window[0]["ts"] < cutoff:
            self.prosody_window.popleft()

        rows = list(self.prosody_window)
        total_dur = sum(r["dur"] for r in rows) or 1e-6
        voiced_rows = [r for r in rows if r["speech"]]
        voiced_dur = sum(r["dur"] for r in voiced_rows)
        pause_ratio = max(0.0, min(1.0, (total_dur - voiced_dur) / total_dur))

        voiced_energy = [r["rms"] for r in voiced_rows]
        mean_energy = (sum(voiced_energy) / len(voiced_energy)) if voiced_energy else 0.0

        pitch_vals = [r["pitch"] for r in voiced_rows if r.get("pitch")]
        if len(pitch_vals) >= 3:
            pitch_mean = sum(pitch_vals) / len(pitch_vals)
            pitch_var = sum((p - pitch_mean) ** 2 for p in pitch_vals) / len(pitch_vals)
            pitch_std = pitch_var ** 0.5
        else:
            pitch_std = 0.0

        if voiced_dur < 5.0:
            monotony = 0.0
        else:
            # 0 (expressive) -> 100 (highly monotone)
            monotony = max(0.0, min(100.0, (1.0 - min(pitch_std / 35.0, 1.0)) * 100.0))

        self.prosody_metrics = {
            "pitch_variance_hz": round(pitch_std, 1),
            "pause_ratio_20s": round(pause_ratio, 3),
            "speaking_energy": round(mean_energy, 4),
            "monotony_score": round(monotony, 1),
            "voiced_seconds_20s": round(voiced_dur, 1),
        }
        return self.prosody_metrics

    def attach_coach_said(self, text: str) -> dict | None:
        """
        Record what the coach said (a streaming chunk) against the most recent
        coaching event, if recent.
        Returns {"index", "text"} for the UI, or None when no event is close enough.
        """
        if not text or not self.events:
            return None
        event = self.events[-1]
        if self.duration_seconds() - event.timestamp > _COACH_SAID_WINDOW_S:
            return None
        # Streaming chunks carry their own word-boundary whitespace.
        # Keep a trailing space (it may be the boundary before the next chunk).
        event.coach_said = re.sub(r"\s+", " ", event.coach_said + text).lstrip()
        return {"index": len(self.events) - 1, "text": event.coach_said.strip()}

    def add_transcript(self, speaker: str, text: str, *, raw: bool = False) -> None:
        """
        Append transcribed speech.

        raw=False: `text` is a complete phrase; merged into the current segment with a space.
        raw=True:  `text` is a streaming chunk from the Live API whose own leading/trailing
                   whitespace marks word boundaries ("frien" + "dly" -> "friendly"), so it is
                   concatenated as-is. A new segment only starts on a word boundary.
        """
        now_rel = round(time.time() - self.session_start, 1)
        s_key = speaker[0]  # "u" or "c" — compact keys reduce context size
        last = self.transcript[-1] if self.transcript else None
        mid_word = raw and bool(text) and not text[0].isspace()
        if (
            last is not None
            and last["s"] == s_key
            and now_rel - last.get("te", last["ts"]) <= _SEGMENT_MAX_GAP_S
            and (now_rel - last["ts"] <= _SEGMENT_MAX_SPAN_S or mid_word)
        ):
            # Merge streaming fragments from the same speaker into one segment.
            if raw:
                last["t"] = re.sub(r"\s+", " ", last["t"] + text).lstrip()
            else:
                last["t"] = f"{last['t']} {text}".strip()
            last["te"] = now_rel
            return
        text = re.sub(r"\s+", " ", text).lstrip() if raw else text
        if not text.strip():
            return
        self.transcript.append({"s": s_key, "t": text, "ts": now_rel, "te": now_rel})
        # Trim oldest entries when over the rolling cap
        if len(self.transcript) > MAX_TRANSCRIPT_SEGMENTS:
            self.transcript = self.transcript[-MAX_TRANSCRIPT_SEGMENTS:]

    def recent_speech_metrics(self, *, wpm_window_s: float = 20.0, filler_window_s: float = 30.0) -> dict:
        """Rolling WPM and unambiguous-filler count from the presenter transcript."""
        now_rel = self.duration_seconds()
        user = [seg for seg in self.transcript if seg.get("s") == "u"]
        wpm_text = " ".join(seg["t"] for seg in user if seg.get("te", seg["ts"]) >= now_rel - wpm_window_s)
        words = len(wpm_text.split())
        window = min(now_rel, wpm_window_s)
        filler_text = " ".join(seg["t"] for seg in user if seg.get("te", seg["ts"]) >= now_rel - filler_window_s)
        return {
            "wpm": round(words / window * 60, 1) if window > 2 else 0.0,
            "words": words,
            "fillers": sum(1 for _ in _SCORING_FILLER_RE.finditer(filler_text)),
            "filler_words": [m.group(0).lower() for m in _SCORING_FILLER_RE.finditer(filler_text)],
        }

    def user_stt_active(self) -> bool:
        """True while the dedicated transcriber (not the coach model) transcribes the presenter."""
        return self.user_stt is not None and not getattr(self.user_stt, "failed", False)

    def user_word_count(self) -> int:
        return sum(len(seg["t"].split()) for seg in self.transcript if seg.get("s") == "u")

    def conversation_tail(self, seconds: float = 120.0, max_chars: int = 2400) -> str:
        """Recent You/Them lines for live content checks."""
        cutoff = self.duration_seconds() - seconds
        label = {"u": "You", "o": "Them"}
        lines = [
            f"{label[seg['s']]}: {seg['t'].strip()}"
            for seg in self.transcript
            if seg.get("s") in label and seg.get("te", seg["ts"]) >= cutoff and seg["t"].strip()
        ]
        text = "\n".join(lines)
        return text[-max_chars:]

    def transcript_filler_count(self) -> int:
        """Filler words in everything the presenter said this session."""
        text = " ".join(seg["t"] for seg in self.transcript if seg.get("s") == "u")
        return sum(1 for _ in _SCORING_FILLER_RE.finditer(text))

    def transcript_stumble_count(self) -> int:
        """Repeated words/phrases ("it it", "on on") in what the presenter said."""
        return sum(count_stumbles(seg["t"]) for seg in self.transcript if seg.get("s") == "u")

    def scored_filler_count(self) -> int:
        """Fillers plus stumbles: both are verbal clutter the listener hears."""
        return max(self.filler_count, self.transcript_filler_count() + self.transcript_stumble_count())

    def trim_setup_chatter(self) -> list[dict]:
        """
        Remove what the presenter said to the room while starting/stopping (first and last
        CHATTER_EDGE_S of their speech): lines in a different script from the rest of the
        talk, and short start/stop/test phrases. Stops at the first real line on each side.
        Returns (and remembers) what was removed.
        """
        user = [seg for seg in self.transcript if seg.get("s") == "u" and (seg.get("t") or "").strip()]
        if len(user) < 3:
            return []
        script = dominant_script([seg["t"] for seg in user])
        first_ts = user[0]["ts"]
        last_te = max(seg.get("te", seg["ts"]) for seg in user)
        removed: list[dict] = []
        drop_ids: set[int] = set()

        def clean(seg: dict) -> bool:
            """True = segment still has real content (stop scanning this edge)."""
            original = seg["t"]
            trimmed = trim_edge_text(original, script)
            if not trimmed or is_setup_chatter(trimmed):
                drop_ids.add(id(seg))
                removed.append({"ts": seg["ts"], "text": original.strip()})
                return False
            if trimmed != original.strip():
                removed.append({"ts": seg["ts"], "text": original.replace(trimmed, " … ").strip(" …") or original})
                seg["t"] = trimmed
            return True

        for seg in user:                                   # leading edge
            if seg["ts"] > first_ts + CHATTER_EDGE_S or clean(seg):
                break
        for seg in reversed(user):                         # trailing edge
            if id(seg) in drop_ids:
                continue
            if seg.get("te", seg["ts"]) < last_te - CHATTER_EDGE_S or clean(seg):
                break
        if drop_ids:
            self.transcript = [seg for seg in self.transcript if id(seg) not in drop_ids]
        removed.sort(key=lambda r: r["ts"])
        self.ignored_chatter = removed
        return removed

    def scored_eye_drops(self) -> int:
        return max(self.eye_contact_drops, self.gaze_away_streaks, self.browser_gaze_streaks)

    def eye_contact_threshold_s(self) -> float:
        return {"virtual": 5.0, "hybrid": 6.5, "in_person": 8.0}.get(self.delivery_context, 5.0)

    def update_browser_gaze(self, gaze: str, *, now: float | None = None) -> dict | None:
        """
        Track a gaze state reported by the browser face tracker.

        gaze: "camera" | "away" (head turned) | "notes" (looking down) | "no_face".
        Returns a confirmation dict the first time a disengaged streak crosses the
        delivery-context threshold, else None. Each streak is counted once.
        """
        now = time.monotonic() if now is None else now
        gaze = (gaze or "").strip().lower()
        if gaze not in {"camera", "away", "notes", "no_face"}:
            return None
        # Sideways head turns are normal room-scanning outside virtual delivery.
        disengaged = gaze in {"notes", "no_face"} or (gaze == "away" and self.delivery_context == "virtual")
        self.browser_gaze = gaze

        if not disengaged:
            self._browser_away_since = 0.0
            self._browser_streak_counted = False
            return None

        if not self._browser_away_since:
            self._browser_away_since = now
            self._browser_streak_counted = False
        seconds_away = now - self._browser_away_since
        threshold = self.eye_contact_threshold_s()
        if seconds_away < threshold or self._browser_streak_counted:
            return None

        self._browser_streak_counted = True
        self.browser_gaze_streaks += 1
        self.record_signal(
            "eye_contact_confirmed",
            measured=round(seconds_away, 1),
            threshold=f">={threshold}s",
            source="browser_face_tracking",
        )
        self.record_timeline(
            "signal_spike",
            f"Look-away {round(seconds_away, 1)}s ({gaze})",
            {"signal": "eye_contact_confirmed", "gaze": gaze},
            enqueue=True,
        )
        self._enqueue({"type": "metric", "key": "eye_contact", "value": self.scored_eye_drops()})
        return {"gaze": gaze, "seconds_away": round(seconds_away, 1), "threshold_s": threshold}

    def stop_drain(self) -> None:
        """Put the sentinel into the queue so the drain task exits cleanly.

        Clears one slot if the queue is full so the sentinel is guaranteed delivery.
        """
        self.live_closed = True
        if self.ws_event_queue.full():
            try:
                self.ws_event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self.ws_event_queue.put_nowait(_DRAIN_STOP)

    def transcript_text(self) -> str:
        if not self.transcript:
            return "(no transcript captured)"
        label = {"u": "Presenter", "c": "Coach", "o": "Other participant"}
        lines = [
            f"[{seg['ts']}s] {label.get(seg['s'], seg['s'])}: {seg['t']}"
            for seg in self.transcript
        ]
        return "\n".join(lines)

    def events_json(self) -> str:
        # Compact JSON — no indent, abbreviated keys to reduce token count.
        # "ev" included only when evidence is populated so tokens aren't wasted.
        rows = []
        for e in self.events:
            row: dict = {"type": e.event_type, "ts": round(e.timestamp), "desc": e.description}
            if e.evidence:
                row["ev"] = e.evidence
            # Only events with coach_said were actually voiced to the presenter.
            row["said"] = e.coach_said or None
            rows.append(row)
        return json.dumps(rows)

    def timeline_json(self) -> str:
        return json.dumps(self.timeline_events)

    def duration_seconds(self) -> float:
        return time.time() - self.session_start

    def _count(self, event_type: str) -> int:
        return {
            "filler": self.filler_count,
            "eye_contact": self.scored_eye_drops(),
            "pace": self.pace_violations,
            "contradiction": self.contradictions,
            "clarity": self.clarity_flags,
            "slide_clarity": self.visual_flags,
            "slide_mismatch": self.mismatch_flags,
        }.get(event_type, 0)
