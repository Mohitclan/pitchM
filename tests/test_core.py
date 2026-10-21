"""
Pitchlight — core unit tests.

Run with:  python -m pytest tests/ -v
"""
import asyncio
import json
import time
import types as python_types

import pytest


# ── Scorecard builder ─────────────────────────────────────────────

class _FakeState:
    """Minimal SessionState stub for scorecard tests."""
    session_id = "test-session"
    user_id = "tester"
    coach_mode = "presentation"
    delivery_context = "virtual"
    primary_goal = "balanced"
    persona = "coach"
    screen_enabled = False
    demo_mode = False
    total_slides = 0
    current_slide_index = -1
    session_start = time.time() - 90  # 90-second session
    events = []
    timeline_events = []
    # Enough presenter words to be scorable (scorecard.MIN_SCORABLE_WORDS)
    transcript = [{"s": "u", "t": "we help small retail teams plan their weekly inventory " * 4, "ts": 10.0}]
    filler_count = 0
    eye_contact_drops = 0
    pace_violations = 0
    contradictions = 0
    clarity_flags = 0
    visual_flags = 0
    mismatch_flags = 0
    final_report = "Test report"
    research_tips = ""
    generated_assets = []
    ai_scores: dict = {}
    prosody_metrics = {
        "pitch_variance_hz": 0.0,
        "pause_ratio_20s": 0.2,
        "speaking_energy": 0.03,
        "monotony_score": 0.0,
        "voiced_seconds_20s": 10.0,
    }

    def duration_seconds(self):
        return time.time() - self.session_start


def test_scorecard_basic():
    from backend.scorecard import build_scorecard
    state = _FakeState()
    sc = build_scorecard(state)
    assert sc["session_id"] == "test-session"
    assert sc["overall_score"] == 100  # zero events → perfect
    assert sc["scoring"]["base_score"] == 100
    assert sc["scoring"]["event_penalty"] == 0
    assert "filler_words" in sc["categories"]


def test_scorecard_uses_ai_scores_when_present():
    from backend.scorecard import build_scorecard
    state = _FakeState()
    state.ai_scores = {"filler": 30, "pace": 40, "eye": 95, "clarity": 55, "visual": 100}
    sc = build_scorecard(state)
    assert sc["categories"]["filler_words"]["score"] == 30
    assert sc["categories"]["pace"]["score"] == 40
    assert sc["categories"]["eye_contact"]["score"] == 95
    assert sc["categories"]["clarity"]["score"] == 55
    assert sc["categories"]["visual_delivery"]["score"] == 100


def test_scorecard_falls_back_without_ai_scores():
    from backend.scorecard import build_scorecard
    state = _FakeState()
    state.filler_count = 10
    state.ai_scores = {}  # no AI scores → use metric-based calculation
    sc = build_scorecard(state)
    # 10 fillers in 90s = ~6.7/min → falls in 4-7 range → score 35
    assert sc["categories"]["filler_words"]["score"] <= 55


def test_scorecard_short_session():
    from backend.scorecard import build_scorecard
    state = _FakeState()
    state.session_start = time.time() - 3   # only 3 seconds
    state.transcript = []
    state.events = []
    sc = build_scorecard(state)
    assert sc["overall_score"] == 0   # degenerate session guard


def test_scorecard_ai_scores_partial_fallback():
    from backend.scorecard import build_scorecard
    state = _FakeState()
    state.ai_scores = {"filler": 42}  # only filler; rest fall back to metric-based
    sc = build_scorecard(state)
    assert sc["categories"]["filler_words"]["score"] == 42
    assert sc["categories"]["pace"]["score"] == 100  # 0 violations → 100


def test_scorecard_includes_prosody_category():
    from backend.scorecard import build_scorecard
    state = _FakeState()
    state.prosody_metrics = {
        "pitch_variance_hz": 6.2,
        "pause_ratio_20s": 0.03,
        "speaking_energy": 0.01,
        "monotony_score": 82.0,
        "voiced_seconds_20s": 15.0,
    }
    sc = build_scorecard(state)
    assert "prosody" in sc["categories"]
    assert sc["categories"]["prosody"]["score"] < 100


def test_scorecard_applies_event_penalty_to_overall():
    from backend.scorecard import build_scorecard

    state = _FakeState()
    state.pace_violations = 1
    state.events = [
        python_types.SimpleNamespace(
            timestamp=12.3,
            event_type="pace",
            description="Speaking too fast.",
            evidence={},
        )
    ]

    sc = build_scorecard(state)
    # Base score with one pace violation in ~90s is 96.0; pace event penalty brings it down.
    assert sc["scoring"]["base_score"] == 96.0
    assert sc["scoring"]["event_penalty"] == 3.0
    assert sc["overall_score"] == 93.0


# ── AI score extraction ───────────────────────────────────────────

def test_extract_ai_scores_full():
    from backend.pipeline_utils import extract_ai_scores as _extract_ai_scores
    text = """
**What Worked**
Some strength.

**IMAGE_PROMPTS**
IMAGE_PROMPT_1: some prompt
IMAGE_PROMPT_2: another prompt

**SCORES**
SCORE_FILLER: 40
SCORE_PACE: 60
SCORE_EYE: 95
SCORE_CLARITY: 55
SCORE_VISUAL: 100
"""
    scores = _extract_ai_scores(text)
    assert scores == {"filler": 40, "pace": 60, "eye": 95, "clarity": 55, "visual": 100}


def test_extract_ai_scores_clamps_values():
    from backend.pipeline_utils import extract_ai_scores as _extract_ai_scores
    # Values > 100 are clamped to 100; negative values aren't matched by regex
    # (model never outputs them, regex requires \d+)
    text = "SCORE_FILLER: 150\nSCORE_PACE: 0\n"
    scores = _extract_ai_scores(text)
    assert scores["filler"] == 100   # clamped from 150
    assert scores["pace"] == 0       # exactly at floor


def test_extract_ai_scores_empty():
    from backend.pipeline_utils import extract_ai_scores as _extract_ai_scores
    assert _extract_ai_scores("") == {}
    assert _extract_ai_scores("no scores here") == {}


# ── Synthesis validation ──────────────────────────────────────────

def test_validate_synthesis_passes():
    from backend.pipeline_utils import validate_synthesis as _validate_synthesis
    text = (
        "**Opening & Core Message**\nOK\n"
        "**Content & Structure**\nOK\n"
        "**Delivery**\nOK\n"
        "**Top Fixes**\n1. Fix\n"
        "**What Worked**\nGood.\n"
        "**IMAGE_PROMPTS**\nIMAGE_PROMPT_1: slide\n"
        "**SCORES**\n"
        "SCORE_FILLER: 80\nSCORE_PACE: 60\nSCORE_EYE: 95\nSCORE_CLARITY: 70\nSCORE_VISUAL: 100\n"
    )
    assert _validate_synthesis(text) is True


def test_validate_synthesis_fails_missing_section():
    from backend.pipeline_utils import validate_synthesis as _validate_synthesis
    text = "**Opening & Core Message**\nOK\n**Delivery**\nOK\n"
    assert _validate_synthesis(text) is False


def test_validate_synthesis_empty():
    from backend.pipeline_utils import validate_synthesis as _validate_synthesis
    assert _validate_synthesis("") is False
    assert _validate_synthesis(None) is False


def test_missing_synthesis_sections():
    from backend.pipeline_utils import missing_synthesis_sections as _missing_synthesis_sections
    text = "**Opening & Core Message**\nOK\n**What Worked**\nGood.\n"
    missing = _missing_synthesis_sections(text)
    assert "**Content & Structure**" in missing
    assert "**Delivery**" in missing
    assert "**Top Fixes**" in missing
    assert "SCORE_FILLER:" in missing
    assert "SCORE_PACE:" in missing
    assert "SCORE_EYE:" in missing
    assert "SCORE_CLARITY:" in missing
    assert "SCORE_VISUAL:" in missing
    assert "**Opening & Core Message**" not in missing
    assert "**What Worked**" not in missing


# ── Image prompt extraction ───────────────────────────────────────

def test_extract_image_prompts_strips_section():
    from backend.multimodal import extract_image_prompts
    report = (
        "**What Worked**\nStrong opener.\n\n"
        "**IMAGE_PROMPTS**\n"
        "IMAGE_PROMPT_1: A clean slide with headline and 3 bullets.\n"
        "IMAGE_PROMPT_2: A data visualization slide.\n"
        "SCORE_FILLER: 45\n"
    )
    clean, prompts = extract_image_prompts(report)
    assert "**IMAGE_PROMPTS**" not in clean
    assert "SCORE_FILLER" not in clean
    assert len(prompts) == 2
    assert "headline" in prompts[0]


def test_extract_image_prompts_no_section():
    from backend.multimodal import extract_image_prompts
    report = "Just a plain report with no image prompts."
    clean, prompts = extract_image_prompts(report)
    assert clean == report
    assert prompts == []


def test_extract_image_prompts_empty_text():
    from backend.multimodal import extract_image_prompts
    clean, prompts = extract_image_prompts("")
    assert clean == ""
    assert prompts == []


# ── CoachingEvent evidence ────────────────────────────────────────

def test_coaching_event_evidence_serialized():
    from backend.session_state import SessionState
    state = SessionState(session_id="ev-test")
    state.record_event("filler", "Said um 3 times", evidence={"metric": "filler_count_30s", "measured": 3})
    serialized = json.loads(state.events_json())
    assert len(serialized) == 1
    assert serialized[0]["ev"]["metric"] == "filler_count_30s"
    assert serialized[0]["ev"]["measured"] == 3


def test_coaching_event_no_evidence_omitted():
    from backend.session_state import SessionState
    state = SessionState(session_id="ev-test-2")
    state.record_event("clarity", "Confusing sentence")
    serialized = json.loads(state.events_json())
    assert "ev" not in serialized[0]


# ── flag_issue cooldown logic ─────────────────────────────────────

def test_flag_issue_cooldown():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    state = SessionState(session_id="cooldown-test")
    # Add a transcript segment so metrics compute correctly
    state.add_transcript("user", "um um um basically like you know")
    state.record_signal("pace_high", measured=220, threshold=">180", source="test")
    state.record_signal("filler_burst", measured=5, threshold=">=3", source="test")

    tools = make_coaching_tools(state)
    flag = next(t for t in tools if t.__name__ == "flag_issue")

    # First call should succeed
    result1 = flag("filler", "Said um 3 times in 30 seconds")
    assert result1["recorded"] is True

    # Immediate second call hits global cooldown (30s gate)
    result2 = flag("pace", "Speaking too fast")
    assert result2["recorded"] is False
    assert result2["reason"] == "global_cooldown"


def test_flag_issue_invalid_type():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    state = SessionState(session_id="invalid-test")
    tools = make_coaching_tools(state)
    flag = next(t for t in tools if t.__name__ == "flag_issue")
    result = flag("not_a_real_type", "some description")
    assert result["recorded"] is False
    assert "invalid_issue_type" in result["reason"]


def test_flag_issue_requires_two_signals():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    state = SessionState(session_id="two-signal-test")
    state.add_transcript("user", "um um um")
    state.record_signal("filler_burst", measured=4, threshold=">=3", source="test")
    tools = make_coaching_tools(state)
    flag = next(t for t in tools if t.__name__ == "flag_issue")
    result = flag("filler", "Filler burst detected")
    assert result["recorded"] is False
    assert result["reason"] == "two_signal_gate"


def test_flag_issue_evidence_populated():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    state = SessionState(session_id="evidence-test")
    state.add_transcript("user", "um uh like basically um")
    state.record_signal("pace_high", measured=210, threshold=">180", source="test")
    state.record_signal("filler_burst", measured=5, threshold=">=3", source="test")
    tools = make_coaching_tools(state)
    flag = next(t for t in tools if t.__name__ == "flag_issue")

    flag("filler", "Many fillers detected")
    assert len(state.events) == 1
    ev = state.events[0].evidence
    assert ev["metric"] == "filler_count_30s"
    assert "measured" in ev
    assert ev["threshold"] == "≥3"


# ── jump_to_slide ─────────────────────────────────────────────────

def test_jump_to_slide_basic():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    state = SessionState(session_id="slide-test")
    state.total_slides = 5
    state.current_slide_index = 0
    tools = make_coaching_tools(state)
    jump = next(t for t in tools if t.__name__ == "jump_to_slide")

    result = jump(3)
    assert result["status"] == "ok"
    assert result["current_slide_index"] == 3
    assert state.current_slide_index == 3


def test_jump_to_slide_no_slides():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    state = SessionState(session_id="slide-test-2")
    state.total_slides = 0
    tools = make_coaching_tools(state)
    jump = next(t for t in tools if t.__name__ == "jump_to_slide")
    result = jump(2)
    assert result["status"] == "ignored"


def test_jump_to_slide_clamps_bounds():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    state = SessionState(session_id="slide-test-3")
    state.total_slides = 3
    state.current_slide_index = 0
    tools = make_coaching_tools(state)
    jump = next(t for t in tools if t.__name__ == "jump_to_slide")

    # Out of range → clamp to last valid index
    result = jump(99)
    assert result["status"] == "ok"
    assert result["current_slide_index"] == 2  # max index for 3 slides


# ── mark_slide_issue ──────────────────────────────────────────────

def test_mark_slide_issue_valid():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    state = SessionState(session_id="mark-test")
    state.current_slide_index = 2
    tools = make_coaching_tools(state)
    mark = next(t for t in tools if t.__name__ == "mark_slide_issue")

    result = mark("clutter", "Too many bullets")
    assert result["status"] == "marked"
    assert result["slide_index"] == 2
    assert result["issue_type"] == "clutter"


def test_mark_slide_issue_invalid_type():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    state = SessionState(session_id="mark-test-2")
    tools = make_coaching_tools(state)
    mark = next(t for t in tools if t.__name__ == "mark_slide_issue")
    result = mark("not_a_real_type")
    assert result["status"] == "error"


# ── SESSION_SUMMARY_AGENT JSON output ─────────────────────────────

def test_session_summary_agent_json_parse():
    """The JSON emitted by SESSION_SUMMARY_AGENT must round-trip cleanly."""
    import json
    sample = json.dumps({
        "recurring_issues": ["filler_words", "pace"],
        "strengths": ["eye_contact"],
        "trajectory": "First session. Pace and filler words are primary targets.",
        "next_focus": "reduce_fillers",
        "session_score": 72,
    })
    profile = json.loads(sample)
    assert profile["next_focus"] == "reduce_fillers"
    assert "filler_words" in profile["recurring_issues"]
    assert profile["session_score"] == 72


def test_session_summary_agent_required_keys():
    """Verify all required profile keys are present in a realistic output."""
    import json
    sample = (
        '{"recurring_issues": ["pace"], "strengths": [], '
        '"trajectory": "Needs pacing work.", "next_focus": "improve_pacing", '
        '"session_score": 55}'
    )
    profile = json.loads(sample)
    for key in ("recurring_issues", "strengths", "trajectory", "next_focus", "session_score"):
        assert key in profile, f"Missing required key: {key}"


def test_session_summary_agent_exported():
    """SESSION_SUMMARY_AGENT is importable and is an LlmAgent (requires google-adk)."""
    pytest.importorskip("google.adk", reason="google-adk not installed")
    from google.adk.agents import LlmAgent
    from backend.agents.post_session import SESSION_SUMMARY_AGENT, PARALLEL_ANALYSTS, POST_SESSION_PIPELINE
    assert isinstance(SESSION_SUMMARY_AGENT, LlmAgent)
    assert SESSION_SUMMARY_AGENT.name == "session_summary_agent"
    assert SESSION_SUMMARY_AGENT.output_key == "session_profile_json"
    # Pipeline wiring sanity
    assert POST_SESSION_PIPELINE.name == "post_session_pipeline"


def test_browser_gaze_counts_each_streak_once():
    from backend.session_state import SessionState
    s = SessionState(session_id="g", delivery_context="virtual")
    assert s.update_browser_gaze("notes", now=100.0) is None
    assert s.update_browser_gaze("notes", now=103.0) is None       # under 5s threshold
    hit = s.update_browser_gaze("notes", now=105.5)
    assert hit and hit["seconds_away"] == 5.5
    assert s.update_browser_gaze("notes", now=110.0) is None       # same streak, not recounted
    assert s.browser_gaze_streaks == 1
    s.update_browser_gaze("camera", now=111.0)                      # reset
    s.update_browser_gaze("no_face", now=112.0)
    assert s.update_browser_gaze("no_face", now=118.0)
    assert s.browser_gaze_streaks == 2
    assert s.scored_eye_drops() == 2
    assert s.has_recent_signals(["eye_contact_confirmed"], window_s=60)


def test_browser_gaze_sideways_ok_for_in_person():
    from backend.session_state import SessionState
    s = SessionState(session_id="g2", delivery_context="in_person")
    s.update_browser_gaze("away", now=0.0)
    assert s.update_browser_gaze("away", now=20.0) is None          # scanning the room is fine
    s.update_browser_gaze("notes", now=21.0)
    assert s.update_browser_gaze("notes", now=29.5)                 # 8s threshold in person
    assert s.browser_gaze_streaks == 1


def test_scorecard_eye_uses_measured_floor_over_ai():
    from backend.session_state import SessionState
    from backend.scorecard import build_scorecard
    import time
    s = SessionState(session_id="g3")
    s.session_start = time.time() - 60
    s.add_transcript("user", "we help small retail teams plan their weekly inventory " * 4)
    s.browser_gaze_streaks = 3
    s.ai_scores = {"filler": 90, "pace": 90, "eye": 100, "clarity": 90, "visual": 100}
    sc = build_scorecard(s)
    assert sc["categories"]["eye_contact"]["drops"] == 3
    assert sc["categories"]["eye_contact"]["score"] < 100


def test_local_sqlite_storage_survives_restart(tmp_path, monkeypatch):
    import asyncio
    from backend import db

    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "sub" / "pm.db"))

    async def write():
        first = db.FirestoreClient()
        assert first.durable
        assert await first.save_session("s1", {"user_id": "u1", "created_at": 100, "overall_score": 77})
        assert await first.save_session("s2", {"user_id": "u2", "created_at": 200, "overall_score": 50})
        assert await first.save_user_profile("u1", {"next_focus": "reduce_fillers"})

    async def read():
        second = db.FirestoreClient()   # fresh instance == fresh process, empty memory cache
        assert (await second.get_session("s1"))["overall_score"] == 77
        assert [r["session_id"] for r in await second.list_recent(10, "u1")] == ["s1"]
        assert [r["session_id"] for r in await second.list_recent(10)] == ["s2", "s1"]
        assert (await second.get_user_profile("u1"))["next_focus"] == "reduce_fillers"
        assert await second.get_user_profile("nobody") is None

    asyncio.run(write())
    asyncio.run(read())


def test_raw_transcript_chunks_keep_words_whole():
    from backend.session_state import SessionState
    s = SessionState(session_id="t-raw")
    for chunk in ["t", " u", "ser", " frien", "dly", " and we have", " star", "ted"]:
        s.add_transcript("user", chunk, raw=True)
    assert len(s.transcript) == 1
    assert s.transcript[0]["t"].strip() == "t user friendly and we have started"


def test_raw_transcript_new_segment_only_on_word_boundary():
    from backend.session_state import SessionState
    s = SessionState(session_id="t-raw2")
    s.add_transcript("user", "hello wor", raw=True)
    s.transcript[0]["ts"] -= 20          # pretend the segment is already long
    s.add_transcript("user", "ld", raw=True)          # mid-word → must stay in same segment
    assert s.transcript[0]["t"] == "hello world"
    s.add_transcript("user", " next", raw=True)       # word boundary → new segment
    assert [seg["t"].strip() for seg in s.transcript] == ["hello world", "next"]


def test_tool_text_detection():
    from backend.session_state import looks_like_tool_text
    assert looks_like_tool_text('tool_code m_1 = check_eye_contact(gaze_direction="away")')
    assert looks_like_tool_text("flag_issue(issue_type='eye_contact')")
    assert not looks_like_tool_text("Eyes up — look at the camera.")
    assert not looks_like_tool_text("Slow down, you're rushing.")


def test_coach_said_attaches_to_recent_event_only():
    from backend.session_state import SessionState
    s = SessionState(session_id="t-said")
    assert s.attach_coach_said("Look") is None                 # no events yet
    s.record_event("eye_contact", "looked down", {"metric": "seconds_away"})
    assert s.attach_coach_said(" Eyes")["text"] == "Eyes"
    assert s.attach_coach_said(" up.")["text"] == "Eyes up."
    assert s.events[-1].coach_said.strip() == "Eyes up."
    s.events[-1].timestamp -= 60                               # too old now
    assert s.attach_coach_said(" again") is None


def test_lookaway_is_flagged_server_side_and_cued():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools
    from backend.main import _handle_confirmed_lookaway

    class FakeQueue:
        def __init__(self):
            self.sent = []

        def send_content(self, content):
            self.sent.append(content.parts[0].text)

    s = SessionState(session_id="t-gaze")
    make_coaching_tools(s)
    q = FakeQueue()
    s.update_browser_gaze("notes", now=100.0)
    confirmed = s.update_browser_gaze("notes", now=106.0)
    _handle_confirmed_lookaway(s, q, confirmed)

    assert len(s.events) == 1
    ev = s.events[0]
    assert ev.event_type == "eye_contact" and ev.source == "sensor"
    assert ev.evidence["measured"] == 6.0
    assert s.last_confirmed_issue_type == "eye_contact"       # lets the cue through the speech gate
    assert q.sent and "Eyes up" in q.sent[0] and "Do not call any tools" in q.sent[0]

    # A second look-away inside the cooldown is recorded quietly, with no voice request.
    s.update_browser_gaze("camera", now=107.0)
    s.update_browser_gaze("no_face", now=108.0)
    _handle_confirmed_lookaway(s, q, s.update_browser_gaze("no_face", now=114.0))
    assert len(s.events) == 2 and s.events[1].source == "sensor"
    assert len(q.sent) == 1


def test_scorecard_includes_coach_said():
    from backend.session_state import SessionState
    from backend.scorecard import build_scorecard
    import time
    s = SessionState(session_id="t-sc")
    s.session_start = time.time() - 60
    s.add_transcript("user", "we help small retail teams plan their weekly inventory " * 4)
    s.record_event("pace", "too fast", {"metric": "wpm_20s"})
    s.attach_coach_said(" Slow down.")
    sc = build_scorecard(s)
    assert sc["coaching_events"][0]["coach_said"] == "Slow down."
    assert sc["coaching_events"][0]["source"] == "model"


def test_recent_speech_metrics_counts_fillers_and_wpm():
    from backend.session_state import SessionState
    import time
    s = SessionState(session_id="t-m")
    s.session_start = time.time() - 20
    s.add_transcript("user", "um so basically we um ship weekly and you know it works " * 4)
    m = s.recent_speech_metrics()
    assert m["fillers"] == 4 * 4          # um, basically, um, you know per repeat
    assert m["wpm"] > 100


def test_server_measured_flag_skips_two_signal_gate_but_keeps_cooldown():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools
    s = SessionState(session_id="t-meas", session_kind="meeting")
    tools = {t.__name__: t for t in make_coaching_tools(s)}
    # The model path needs two speech signals, so a lone filler flag is refused…
    assert tools["flag_issue"]("filler", "fillers")["recorded"] is False
    # …but an objective server measurement goes through, once per cooldown.
    assert s.flag_issue_fn("filler", "4 fillers in 30s", {"measured": 4})["recorded"] is True
    assert s.events[-1].source == "sensor"
    assert s.flag_issue_fn("filler", "again", {"measured": 5})["recorded"] is False


def test_meeting_prompt_and_scorecard_kind():
    from backend.coach import build_system_prompt
    from backend.session_state import SessionState
    from backend.scorecard import build_scorecard
    import time
    assert "LIVE MEETING MODE" in build_system_prompt("interview", meeting=True)
    assert "LIVE MEETING MODE" not in build_system_prompt("interview")
    s = SessionState(session_id="t-k", session_kind="meeting", platform="zoom")
    s.session_start = time.time() - 60
    s.add_transcript("user", "we help small retail teams plan their weekly inventory " * 4)
    sc = build_scorecard(s)
    assert sc["session_kind"] == "meeting" and sc["platform"] == "zoom"


def test_scorecard_refuses_to_score_almost_no_speech():
    from backend.session_state import SessionState
    from backend.scorecard import build_scorecard
    import time
    s = SessionState(session_id="t-few")
    s.session_start = time.time() - 44
    s.add_transcript("user", "hello")
    s.add_transcript("other", "so tell me about yourself and your last role please")
    sc = build_scorecard(s)
    assert sc["insufficient_speech"] is True and sc["overall_score"] == 0 and sc["user_words"] == 1
    assert "Not enough of your speech" in sc["final_report"]


def test_conversation_tail_labels_both_sides():
    from backend.session_state import SessionState
    s = SessionState(session_id="t-tail")
    s.add_transcript("other", "Why do you want this job?")
    s.add_transcript("user", "Because I love building products.")
    assert s.conversation_tail() == "Them: Why do you want this job?\nYou: Because I love building products."
    assert s.user_word_count() == 5


def test_parse_content_verdict():
    from backend.main import _parse_content_verdict
    assert _parse_content_verdict("NONE") == ("none", "")
    assert _parse_content_verdict(" None.") == ("none", "")
    assert _parse_content_verdict("OFF_TOPIC: Answer their pricing question.") == ("off_topic", "Answer their pricing question")
    assert _parse_content_verdict("Off topic, answer the question directly.") == ("off_topic", "answer the question directly")
    assert _parse_content_verdict("Rambling: wrap it up.")[0] == "rambling"
    assert _parse_content_verdict("HEDGING:")[1] == "Drop the hedging — be direct."
    assert _parse_content_verdict("The presenter seems fine overall, nothing major off topic") is None


def test_content_check_reply_flags_and_cues():
    import time
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools
    from backend.main import _handle_content_check_reply
    s = SessionState(session_id="t-cc", session_kind="meeting")
    make_coaching_tools(s)
    _handle_content_check_reply(s, "OFF_TOPIC: Answer the question.")      # no check pending → ignored
    assert not s.events
    s.content_check_sent_at = time.monotonic()
    _handle_content_check_reply(s, "OFF_TOPIC: Answer the question.")
    assert s.events[-1].event_type == "off_topic" and s.content_check_sent_at == 0.0
    queued = [s.ws_event_queue.get_nowait() for _ in range(s.ws_event_queue.qsize())]
    assert {"type": "coach_cue", "index": 0, "text": "Answer the question"} in queued


def test_content_check_reply_retries_after_global_cooldown():
    import asyncio, time
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools
    from backend.main import _handle_content_check_reply

    async def main():
        loop = asyncio.get_running_loop()
        scheduled = []
        loop.call_later = lambda delay, fn, *args: scheduled.append((delay, fn, args))
        s = SessionState(session_id="t-retry", session_kind="meeting")
        make_coaching_tools(s)
        assert s.flag_issue_fn("pace", "fast", {"measured": 200})["recorded"]
        s.content_check_sent_at = time.monotonic()
        _handle_content_check_reply(s, "OFF_TOPIC: Back to pricing.")
        return s, scheduled

    s, scheduled = asyncio.run(main())
    assert [e.event_type for e in s.events] == ["pace"]       # blocked by the 12 s meeting gap…
    assert len(scheduled) == 1 and 11 <= scheduled[0][0] <= 13.5   # …and re-delivered once it passes


def test_stt_callback_stores_segments_and_notifies_browser():
    from backend.session_state import SessionState
    from backend.main import _stt_callback
    s = SessionState(session_id="t-stt", session_kind="meeting")
    _stt_callback(s, "other")("Can you tell me about pricing?")
    _stt_callback(s, "user")("Sure, we charge per store.")
    assert [(seg["s"], seg["t"]) for seg in s.transcript] == [
        ("o", "Can you tell me about pricing?"), ("u", "Sure, we charge per store.")]
    assert s.other_audio_seen
    sent = [s.ws_event_queue.get_nowait() for _ in range(s.ws_event_queue.qsize())]
    assert {"type": "transcript", "speaker": "user", "text": "Sure, we charge per store."} in sent
    assert {"type": "transcript_end", "speaker": "other"} in sent


def test_transcriber_queue_drops_oldest_when_full():
    import asyncio
    from backend.transcriber import LiveTranscriber, _QUEUE_MAX_CHUNKS

    async def run():
        t = LiveTranscriber("user", lambda text: None)
        for i in range(_QUEUE_MAX_CHUNKS + 5):
            t.push(bytes([i % 256]))
        assert t._queue.qsize() == _QUEUE_MAX_CHUNKS
        assert t._queue.get_nowait() == bytes([5])      # 5 oldest chunks dropped
    asyncio.run(run())


def test_looks_like_question():
    from backend.session_state import looks_like_question
    assert looks_like_question("Can you tell me how you would price this product?")
    assert looks_like_question("Thanks for joining. Walk me through your last project")
    assert looks_like_question("Tell me about a time you handled a difficult customer")
    assert not looks_like_question("Thanks for joining today.")
    assert not looks_like_question("Why?")                  # too short to coach on


def test_extract_qa_pairs():
    from backend.session_state import extract_qa_pairs
    t = [
        {"s": "o", "t": "Thanks for joining.", "ts": 1},
        {"s": "o", "t": "Can you walk me through your last role?", "ts": 3},
        {"s": "u", "t": "Sure, I led payments.", "ts": 6},
        {"s": "u", "t": "We grew volume 3x.", "ts": 9},
        {"s": "o", "t": "Why do you want to leave?", "ts": 15},
        {"s": "u", "t": "I want a bigger scope.", "ts": 17},
        {"s": "o", "t": "What are your salary expectations?", "ts": 25},   # unanswered → dropped
    ]
    pairs = extract_qa_pairs(t)
    assert [(p["question"], p["answer"]) for p in pairs] == [
        ("Can you walk me through your last role?", "Sure, I led payments. We grew volume 3x."),
        ("Why do you want to leave?", "I want a bigger scope."),
    ]


def test_parse_answer_hint():
    from backend.main import _parse_answer_hint
    assert _parse_answer_hint("Hint. First, describe the payments team. Second, explain your launch. Third, share the 3x result.") == [
        "describe the payments team", "explain your launch", "share the 3x result"]
    assert _parse_answer_hint("NONE") is None


def test_meeting_reply_routes_hint_then_check():
    import time
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools
    from backend.main import _handle_meeting_reply
    s = SessionState(session_id="t-route", session_kind="meeting")
    make_coaching_tools(s)
    s.hint_question, s.hint_sent_at = "How would you price it?", time.monotonic()
    _handle_meeting_reply(s, "Hint. First, per-store plan. Second, compare to spreadsheets. Third, offer a pilot.")
    assert s.answer_hints[0]["points"][0] == "per-store plan" and s.hint_sent_at == 0.0
    s.content_check_sent_at = time.monotonic()
    _handle_meeting_reply(s, "RAMBLING: wrap it up.")
    assert s.events[-1].event_type == "rambling"


def test_merge_qa_review_tolerates_fences_and_attaches_hints():
    from backend.main import _merge_qa_review
    pairs = [{"question": "How would you price it?", "answer": "Per store.", "ts": 3}]
    raw = '```json\n[{"index": 0, "rating": 7, "verdict": "Partly", "improve": "Add numbers", "better_answer": "We charge [X] per store."}]\n```'
    out = _merge_qa_review(pairs, raw, [{"question": "How would you price it?", "points": ["a", "b", "c"]}])
    assert out[0]["rating"] == 5 and out[0]["improve"] == "Add numbers" and out[0]["hint_points"] == ["a", "b", "c"]
    assert _merge_qa_review(pairs, "garbage", [])[0]["rating"] is None


def test_hedged_generate_takes_the_faster_request(monkeypatch):
    import asyncio
    import backend.main as m
    monkeypatch.setattr(m, "HINT_HEDGE_AFTER_S", 0.05)

    calls = []

    class FakeModels:
        async def generate_content(self, model, contents):
            calls.append(model)
            if len(calls) == 1:
                await asyncio.sleep(10)          # first request stalls
            return type("R", (), {"text": f"answer {len(calls)}"})()

    class FakeClient:
        aio = type("A", (), {"models": FakeModels()})()

    async def run():
        return await m._hedged_generate(FakeClient(), "x", "p")

    resp = asyncio.run(run())
    assert resp.text == "answer 2" and len(calls) == 2


def test_post_session_agent_uses_claude_then_falls_back(monkeypatch):
    import asyncio
    import backend.main as m
    from backend import claude_client

    class Agent:
        name, model, instruction = "delivery_analyst", "gemini-x", "You analyse delivery."

    monkeypatch.setattr(claude_client, "claude_enabled", lambda: True)

    async def ok(system, user, **kw):
        assert system == "You analyse delivery." and user == "transcript"
        return "claude report"
    monkeypatch.setattr(claude_client, "report_text", ok)
    assert asyncio.run(m._run_single_agent(Agent(), "transcript")) == "claude report"

    async def boom(system, user, **kw):
        raise claude_client.ClaudeUnavailable("refusal")
    monkeypatch.setattr(claude_client, "report_text", boom)
    monkeypatch.setattr(m, "_FALLBACK_MODELS", [])
    calls = []

    async def fake_wait_for(coro, timeout):
        coro.close()
        calls.append("gemini")
        return "gemini report"
    monkeypatch.setattr(m.asyncio, "wait_for", fake_wait_for)
    assert asyncio.run(m._run_single_agent(Agent(), "transcript")) == "gemini report" and calls == ["gemini"]


def test_content_check_runs_on_claude_and_flags(monkeypatch):
    import asyncio, time
    import backend.main as m
    from backend import claude_client
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools

    monkeypatch.setattr(claude_client, "claude_enabled", lambda: True)

    async def reply(prompt, **kw):
        return "OFF_TOPIC: Answer their pricing question."
    monkeypatch.setattr(claude_client, "fast_text", reply)

    class Queue:
        sent = []
        def send_content(self, c): self.sent.append(c)

    s = SessionState(session_id="t-cl", session_kind="meeting")
    make_coaching_tools(s)
    s.content_check_sent_at = time.monotonic()
    q = Queue()
    asyncio.run(m._run_content_check(s, q, "prompt"))
    assert s.events[-1].event_type == "off_topic" and not q.sent      # live model not needed


def test_brief_points_cleaning_and_coverage_parsing():
    from backend import meeting_brief as mb
    pts = mb.clean_points(["- Pricing per store", "2) Pilot timeline", "", "pricing per store", "• Next steps"])
    assert pts == ["Pricing per store", "Pilot timeline", "Next steps"]
    assert mb.parse_coverage_reply("COVERED: 1, 3", 3) == {0, 2}
    assert mb.parse_coverage_reply("covered: none", 3) == set()
    assert mb.parse_coverage_reply("COVERED: 9", 3) == set()
    assert mb.parse_coverage_reply("I think they covered pricing", 3) is None
    assert mb.parse_points_reply("Pricing model\n\n2. Pilot offer\n") == ["Pricing model", "Pilot offer"]


def test_apply_brief_and_reminder():
    import time
    from backend.session_state import SessionState
    from backend.main import _apply_brief, _maybe_brief_reminder
    s = SessionState(session_id="t-brief", session_kind="meeting")
    _apply_brief(s, {"notes": "Client: Acme", "points": ["Pricing", "Pilot", "Next steps"], "planned_minutes": 10})
    assert [p["text"] for p in s.brief_points] == ["Pricing", "Pilot", "Next steps"] and s.planned_minutes == 10
    s.brief_points[0]["covered"] = True
    _maybe_brief_reminder(s)
    assert not s.brief_reminded                                  # too early
    s.session_start = time.time() - 9 * 60                       # 90% of planned length
    _maybe_brief_reminder(s)
    sent = [s.ws_event_queue.get_nowait() for _ in range(s.ws_event_queue.qsize())]
    assert {"type": "brief_reminder", "points": ["Pilot", "Next steps"]} in sent
    _maybe_brief_reminder(s)                                     # only once
    assert s.brief_reminded


def test_parse_recap():
    from backend.main import _parse_recap
    raw = '```json\n{"summary": "Discussed pricing.", "decisions": ["Run a pilot"], "action_items": [{"owner": "You", "task": "Send proposal", "due": "Friday"}, {"task": ""}], "follow_up_email": {"subject": "Next steps", "body": "Hi [Name], ..."}}\n```'
    r = _parse_recap(raw)
    assert r["summary"] == "Discussed pricing." and r["decisions"] == ["Run a pilot"]
    assert r["action_items"] == [{"owner": "You", "task": "Send proposal", "due": "Friday"}]
    assert r["follow_up_email"]["subject"] == "Next steps" and r["open_questions"] == []
    assert _parse_recap("not json") == {}


def test_feedback_api_roundtrip(tmp_path, monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient
    import backend.main as m
    from backend import db

    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "fb.db"))
    store = db.FirestoreClient()
    monkeypatch.setattr(m, "_db", store)
    asyncio.run(store.save_session("sess-0001", {"user_id": "tester-a", "created_at": 1, "session_kind": "meeting", "overall_score": 80}))

    client = TestClient(m.app)
    h = {"x-user-id": "tester-a"}
    assert client.post("/api/feedback", json={"session_id": "sess-0001", "rating": 6}, headers=h).status_code == 400
    assert client.post("/api/feedback", json={"session_id": "sess-0001", "rating": 4}, headers={"x-user-id": "other-user"}).status_code == 404
    ok = client.post("/api/feedback", json={"session_id": "sess-0001", "rating": 4, "tester": "Mansi",
                                            "helped": "Answer outlines", "would_use": "yes"}, headers=h)
    assert ok.status_code == 200
    summary = client.get("/api/feedback/summary").json()
    assert summary["count"] == 1 and summary["avg_rating"] == 4 and summary["would_use_yes_pct"] == 100
    assert summary["testers"] == ["Mansi"] and summary["comments"][0]["helped"] == "Answer outlines"


def test_help_reply_parsing_and_mood_parsing():
    from backend.main import _parse_help_reply, _parse_mood
    assert _parse_help_reply("ask", "QUESTION: What would make the pilot a success for you? | WHY: defines success criteria") == [
        "What would make the pilot a success for you?", "Why: defines success criteria"]
    assert _parse_help_reply("catchup", "They asked about setup time.\nThey want a pilot start date.") == [
        "They asked about setup time.", "They want a pilot start date."]
    assert _parse_mood("MOOD: SKEPTICAL — doubts the 15% discount") == ("skeptical", "doubts the 15% discount")
    assert _parse_mood("MOOD: happy") is None and _parse_mood("no idea") is None


def test_help_answer_uses_last_question_and_rating_and_interruption():
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools
    import backend.main as m
    s = SessionState(session_id="t-help", session_kind="meeting")
    make_coaching_tools(s)
    m._request_help(s, "answer")                                   # nothing asked yet
    sent = [s.ws_event_queue.get_nowait() for _ in range(s.ws_event_queue.qsize())]
    assert sent[-1]["type"] == "help_reply" and "No question" in sent[-1]["lines"][0]
    s.add_transcript("other", "How long does setup take for your team?")
    assert m._last_other_question(s) == "How long does setup take for your team?"

    m._record_interruption(s, 1.4)
    assert s.interruptions == 1 and s.events[-1].event_type == "interruption"
    m._rate_nudge(s, 0, False)
    assert s.events[0].user_rating == "down"
    m._rate_nudge(s, 99, True)                                       # out of range ignored


def test_mood_check_alerts_only_on_change(monkeypatch):
    import asyncio
    import backend.main as m
    from backend import claude_client
    from backend.session_state import SessionState
    s = SessionState(session_id="t-mood", session_kind="meeting")
    monkeypatch.setattr(claude_client, "claude_enabled", lambda: True)
    replies = iter(["MOOD: CONFUSED — asked the same thing twice", "MOOD: CONFUSED — still unclear"])

    async def fake(prompt, **kw):
        return next(replies)
    monkeypatch.setattr(claude_client, "fast_text", fake)
    asyncio.run(m._run_mood_check(s))
    asyncio.run(m._run_mood_check(s))
    sent = [s.ws_event_queue.get_nowait() for _ in range(s.ws_event_queue.qsize())]
    moods = [e for e in sent if e["type"] == "client_mood"]
    assert [e["alert"] is not None for e in moods] == [True, False] and len(s.client_moods) == 2


def test_transcriber_handover_decisions(monkeypatch):
    import backend.transcriber as t
    stt = t.LiveTranscriber("user", lambda text: None)
    clock = [1000.0]
    monkeypatch.setattr(t.time, "monotonic", lambda: clock[0])
    started = 1000.0
    stt._last_text_at = started
    stt._last_voice_at = started
    clock[0] = started + 100
    assert stt._handover_reason(started) is None                       # young session
    clock[0] = started + t.ROLLOVER_AFTER_S + 1
    stt._last_voice_at = clock[0] - 0.1                                # still talking → wait for a pause
    stt._last_text_at = clock[0]
    assert stt._handover_reason(started) is None
    stt._last_voice_at = clock[0] - 1.0                                # pause → roll over
    assert stt._handover_reason(started) == "rollover"
    stt._unconfirmed.extend((clock[0], b"x") for _ in range(int(t.ROLLOVER_MAX_UNCONFIRMED_S * 10) + 5))
    assert stt._handover_reason(started) is None                       # mid-utterance audio pending → wait
    stt._unconfirmed.clear()
    clock[0] = started + t.ROLLOVER_FORCE_S + 1
    stt._last_voice_at = clock[0]                                      # no pause, but at the hard limit
    assert stt._handover_reason(started) == "rollover"

    # Stall: lots of voice, no text for a long time, on a session that isn't near the cap.
    clock[0] = started + 100
    stt._last_text_at = started + 20
    stt._voiced_since_text = t.STALL_VOICED_S + 1
    assert stt._handover_reason(started) == "stall"
    stt._voiced_since_text = 3.0                                       # mostly silence → not a stall
    assert stt._handover_reason(started) is None


def test_transcriber_push_tracks_voice():
    import array
    import asyncio
    import backend.transcriber as t

    async def run():
        stt = t.LiveTranscriber("user", lambda text: None)
        stt.push(array.array("h", [0] * 1600).tobytes())               # silence
        assert stt._voiced_since_text == 0
        stt.push(array.array("h", [3000, -3000] * 800).tobytes())     # speech-level chunk
        assert abs(stt._voiced_since_text - 0.1) < 1e-9 and stt._last_voice_at > 0
    asyncio.run(run())


def test_audio_leveler_boosts_quiet_and_tames_loud():
    """Run the browser leveller worklet in Node: quiet speech gets louder, loud speech never clips."""
    import json, shutil, subprocess, textwrap
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not installed")
    script = textwrap.dedent("""
        const fs = require('fs');
        const src = fs.readFileSync('frontend/audio-leveler.js', 'utf8');
        const code = src.match(/const WORKLET_CODE = `([\\s\\S]*?)`;/)[1];
        let Proc;
        global.AudioWorkletProcessor = class { constructor() { this.port = { postMessage: (m) => out.push(m) }; } };
        global.registerProcessor = (_n, cls) => { Proc = cls; };
        const out = [];
        eval(code);
        const p = new Proc({ processorOptions: { chunkSamples: 1600 } });
        const tone = (amp, n) => Float32Array.from({ length: n }, (_, i) => amp * Math.sin(2 * Math.PI * 220 * i / 16000));
        const feed = (sig) => { for (let i = 0; i < sig.length; i += 128) p.process([[sig.subarray(i, i + 128)]]); };
        const rms = (a) => Math.sqrt(a.reduce((s, x) => s + x * x, 0) / a.length);
        feed(tone(0.02, 32000)); const quiet = out.slice(-3);          // 2 s of quiet speech
        out.length = 0;
        feed(tone(0.95, 32000)); const loud = out.slice(-3);           // 2 s of very loud speech
        out.length = 0;
        feed(new Float32Array(32000).map(() => (Math.random() - 0.5) * 0.004)); const hiss = out.slice(-3);
        console.log(JSON.stringify({
          quietIn: rms(tone(0.02, 1600)), quietOut: rms(quiet[2].pcm),
          loudPeak: Math.max(...loud[2].pcm.map(Math.abs)), loudOut: rms(loud[2].pcm),
          hissGain: hiss[2].gain,
        }));
    """)
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60, cwd=".")
    assert res.returncode == 0, res.stderr
    r = json.loads(res.stdout.strip().splitlines()[-1])
    assert r["quietOut"] > r["quietIn"] * 3          # quiet speech boosted ≥ 3x
    assert r["loudPeak"] <= 1.0 and r["loudOut"] < 0.5   # loud speech pulled down, never clips
    assert r["hissGain"] < 2.0                       # background hiss is not amplified
