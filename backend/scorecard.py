"""
Pitchlight — scorecard builder.
Converts SessionState into a structured display-ready scorecard dict.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.session_state import SessionState

# Minimum meaningful session length; anything shorter gets a null scorecard.
_MIN_SESSION_SEC = 5.0
# Fewer presenter words than this can't be scored honestly (e.g. transcription failed).
MIN_SCORABLE_WORDS = 25

# Goal → category weights (must sum to 1.0).
# When a user selects a focus goal, the overall score emphasises that dimension
# so the final number reflects what they were actually practising.
_GOAL_WEIGHTS: dict[str, dict[str, float]] = {
    "balanced":           {"filler": 0.22, "eye": 0.23, "pace": 0.20, "clarity": 0.15, "visual": 0.10, "prosody": 0.10},
    "reduce_fillers":     {"filler": 0.40, "eye": 0.16, "pace": 0.14, "clarity": 0.13, "visual": 0.07, "prosody": 0.10},
    "improve_pacing":     {"filler": 0.10, "eye": 0.14, "pace": 0.35, "clarity": 0.11, "visual": 0.06, "prosody": 0.24},
    "improve_confidence": {"filler": 0.10, "eye": 0.30, "pace": 0.18, "clarity": 0.20, "visual": 0.05, "prosody": 0.17},
    "improve_structure":  {"filler": 0.08, "eye": 0.12, "pace": 0.12, "clarity": 0.45, "visual": 0.12, "prosody": 0.11},
}

# Severity weights used to apply an overall-session penalty when the coach had
# to interrupt. Category scores remain dimension-specific; this normalizes the
# "overall" grade so sessions with multiple flags do not still show as perfect.
_EVENT_SEVERITY = {
    "filler": 2.0,
    "eye_contact": 2.0,
    "pace": 3.0,
    "clarity": 3.0,
    "contradiction": 4.0,
    "slide_clarity": 2.0,
    "slide_mismatch": 3.0,
    "monologue": 1.0,
    "talk_share": 1.0,
}


def build_scorecard(state: "SessionState") -> dict:
    duration_sec = state.duration_seconds()
    duration_min = duration_sec / 60
    created_at = int(state.session_start)

    # Guard: degenerate sessions (immediate disconnect / no audio) must not
    # score 100 just because all counters happen to be zero.
    user_words = sum(len(seg.get("t", "").split()) for seg in state.transcript if seg.get("s") == "u")
    insufficient = user_words < MIN_SCORABLE_WORDS
    if duration_sec < _MIN_SESSION_SEC or insufficient:
        return {
            "session_id": state.session_id,
            "user_id": state.user_id,
            "coach_mode": state.coach_mode,
            "session_kind": getattr(state, "session_kind", "practice"),
            "platform": getattr(state, "platform", ""),
            "delivery_context": state.delivery_context,
            "primary_goal": state.primary_goal,
            "screen_enabled": state.screen_enabled,
            "demo_mode": state.demo_mode,
            "total_slides": state.total_slides,
            "current_slide_index": state.current_slide_index,
            "created_at": created_at,
            "duration_seconds": round(duration_sec, 1),
            "overall_score": 0,
            "insufficient_speech": True,
            "user_words": user_words,
            "categories": {},
            "coaching_events": [
                {
                    "timestamp": round(e.timestamp, 1),
                    "type": e.event_type,
                    "text": e.description,
                    "evidence": e.evidence,
                    "coach_said": (getattr(e, "coach_said", "") or "").strip(),
                    "source": getattr(e, "source", "model"),
                }
                for e in state.events
            ],
            "timeline_events": state.timeline_events,
            "transcript": state.transcript,
            "final_report": state.final_report or (
                f"Not enough of your speech was captured to score this session ({user_words} words). "
                "Check that the right microphone is selected and speak for at least 30 seconds."
            ),
            "research_tips": state.research_tips,
            "generated_assets": state.generated_assets,
            "prosody": state.prosody_metrics,
            **_session_extras(state),
        }

    # Use holistic AI scores from the synthesis agent when available;
    # fall back to metric-based scoring for any dimension not covered.
    ai = getattr(state, "ai_scores", {})
    # Filler and eye contact are measured directly (transcript / gaze checks), so the
    # AI score may lower them but never hide what was measured. The report agents
    # only see the transcript, not video, so they can't judge eye contact alone.
    filler_measured = _score_filler(_filler_count(state), duration_min)
    eye_measured    = _score_eye(_eye_drops(state), duration_min)
    filler_score = min(ai["filler"], filler_measured) if "filler" in ai else filler_measured
    eye_score    = min(ai["eye"], eye_measured)       if "eye"    in ai else eye_measured
    pace_score   = ai["pace"]   if "pace"   in ai else _score_pace(state.pace_violations, duration_min)
    clarity_score = ai["clarity"] if "clarity" in ai else _score_clarity(state.contradictions, state.clarity_flags, duration_min)
    visual_score  = ai["visual"]  if "visual"  in ai else _score_visual(state.visual_flags, state.mismatch_flags, duration_min)
    prosody_score = _score_prosody(getattr(state, "prosody_metrics", {}))

    goal = (getattr(state, "primary_goal", None) or "balanced")
    w = _GOAL_WEIGHTS.get(goal, _GOAL_WEIGHTS["balanced"])

    overall_base = (
        filler_score * w["filler"]
        + eye_score * w["eye"]
        + pace_score * w["pace"]
        + clarity_score * w["clarity"]
        + visual_score * w["visual"]
        + prosody_score * w["prosody"]
    )
    event_penalty = _overall_event_penalty(state)
    overall = round(max(0.0, overall_base - event_penalty), 1)

    return {
        "session_id": state.session_id,
        "user_id": state.user_id,
        "coach_mode": state.coach_mode,
        "session_kind": getattr(state, "session_kind", "practice"),
        "platform": getattr(state, "platform", ""),
        "delivery_context": state.delivery_context,
        "primary_goal": state.primary_goal,
        "screen_enabled": state.screen_enabled,
        "demo_mode": state.demo_mode,
        "total_slides": state.total_slides,
        "current_slide_index": state.current_slide_index,
        "created_at": created_at,
        "duration_seconds": round(duration_sec, 1),
        "overall_score": overall,
        "scoring": {
            "base_score": round(overall_base, 1),
            "event_penalty": round(event_penalty, 1),
            "goal": goal,
            "weights": w,
        },
        "categories": {
            "filler_words": {
                "score": filler_score,
                "count": _filler_count(state),
                "stumbles": _call(state, "transcript_stumble_count"),
                "label": _grade(filler_score),
            },
            "eye_contact": {
                "score": eye_score,
                "drops": _eye_drops(state),
                "label": _grade(eye_score),
            },
            "pace": {
                "score": pace_score,
                "violations": state.pace_violations,
                "label": _grade(pace_score),
            },
            "clarity": {
                "score": clarity_score,
                "contradictions": state.contradictions,
                "flags": state.clarity_flags,
                "label": _grade(clarity_score),
            },
            "visual_delivery": {
                "score": visual_score,
                "slide_clarity_flags": state.visual_flags,
                "slide_mismatch_flags": state.mismatch_flags,
                "label": _grade(visual_score),
            },
            "prosody": {
                "score": prosody_score,
                "pitch_variance_hz": float(state.prosody_metrics.get("pitch_variance_hz", 0.0)),
                "pause_ratio_20s": float(state.prosody_metrics.get("pause_ratio_20s", 0.0)),
                "speaking_energy": float(state.prosody_metrics.get("speaking_energy", 0.0)),
                "monotony_score": float(state.prosody_metrics.get("monotony_score", 0.0)),
                "label": _grade(prosody_score),
            },
        },
        "coaching_events": [
            {
                "timestamp": round(e.timestamp, 1),
                "type": e.event_type,
                "text": e.description,
                "evidence": e.evidence,
                "coach_said": (getattr(e, "coach_said", "") or "").strip(),
                "source": getattr(e, "source", "model"),
                "user_rating": getattr(e, "user_rating", ""),
            }
            for e in state.events
        ],
        "timeline_events": state.timeline_events,
        "transcript": state.transcript,
        "final_report": state.final_report,
        "research_tips": state.research_tips,
        "generated_assets": state.generated_assets,
        "prosody": state.prosody_metrics,
        "answer_hints": getattr(state, "answer_hints", []),
        "qa_review": getattr(state, "qa_review", []),
        "brief": {
            "notes": getattr(state, "brief_notes", ""),
            "points": getattr(state, "brief_points", []),
            "planned_minutes": getattr(state, "planned_minutes", 0),
        },
        "recap": getattr(state, "recap", {}),
        "client_moods": getattr(state, "client_moods", []),
        "interruptions": getattr(state, "interruptions", 0),
        "transcription_health": getattr(state, "transcription_health", {}),
        **_session_extras(state),
    }


def _session_extras(state) -> dict:
    """Meeting/practice extras saved with every session (both scored and unscored)."""
    return {
        "client": getattr(state, "client_name", ""),
        "previous_call": getattr(state, "previous_call", {}),
        "commitments": getattr(state, "commitments", []),
        "talk_stats": getattr(state, "talk_stats", {}),
        "rewrites": getattr(state, "rewrites", []),
        "ignored_chatter": getattr(state, "ignored_chatter", []),
        "facts": getattr(state, "facts", []),
        "pitch": getattr(state, "pitch", {}),
        "practice_meeting": bool(getattr(state, "practice_meeting", False)),
        "script_result": getattr(state, "script_result", {}),
        "readiness": getattr(state, "readiness", {}),
        "roleplay_role": getattr(state, "roleplay_role", ""),
        "live_advice": getattr(state, "live_advice", []),
        "objections": getattr(state, "objections", []),
        "open_questions": getattr(state, "open_questions", 0),
        "chattiness": getattr(state, "chattiness", "balanced"),
        "brief": {
            "notes": getattr(state, "brief_notes", ""),
            "points": getattr(state, "brief_points", []),
            "planned_minutes": getattr(state, "planned_minutes", 0),
        },
        "recap": getattr(state, "recap", {}),
    }


def _call(state, name: str, default: int = 0) -> int:
    fn = getattr(state, name, None)
    return fn() if callable(fn) else default


def _filler_count(state) -> int:
    # Transcript-derived count when available; interruption count otherwise.
    fn = getattr(state, "scored_filler_count", None)
    return fn() if callable(fn) else state.filler_count


def _eye_drops(state) -> int:
    fn = getattr(state, "scored_eye_drops", None)
    return fn() if callable(fn) else state.eye_contact_drops


def _score_filler(count: int, duration_min: float) -> int:
    if duration_min <= 0:
        return 100
    rate = count / duration_min
    if rate == 0:   return 100
    if rate < 1:    return 90
    if rate < 2:    return 75
    if rate < 4:    return 55
    if rate < 7:    return 35
    return 15


def _score_eye(drops: int, duration_min: float) -> int:
    if duration_min <= 0:
        return 100
    rate = drops / duration_min
    if rate == 0:   return 100
    if rate < 1:    return 85
    if rate < 2:    return 65
    if rate < 4:    return 40
    return 20


def _score_pace(violations: int, duration_min: float) -> int:
    if duration_min <= 0:
        return 100
    rate = violations / duration_min
    if rate == 0:   return 100
    if rate < 1:    return 80
    if rate < 2:    return 60
    return 30


def _score_clarity(contradictions: int, clarity_flags: int, duration_min: float = 1.5) -> int:
    total = contradictions + clarity_flags
    if total == 0:
        return 100
    rate = total / max(duration_min, 0.5)  # issues per minute
    if rate < 0.5:  return 85
    if rate < 1.0:  return 70
    if rate < 2.0:  return 50
    if rate < 3.5:  return 30
    return 15


def _score_visual(slide_flags: int, mismatch_flags: int, duration_min: float) -> int:
    if duration_min <= 0:
        return 100
    weighted = (slide_flags * 0.7) + (mismatch_flags * 1.1)
    rate = weighted / duration_min
    if rate == 0:
        return 100
    if rate < 0.7:
        return 85
    if rate < 1.4:
        return 65
    if rate < 2.2:
        return 45
    return 25


def _score_prosody(prosody: dict) -> int:
    if not prosody:
        return 100

    voiced = float(prosody.get("voiced_seconds_20s") or 0.0)
    if voiced < 5.0:
        return 100

    monotony = float(prosody.get("monotony_score") or 0.0)
    pause_ratio = float(prosody.get("pause_ratio_20s") or 0.0)
    energy = float(prosody.get("speaking_energy") or 0.0)

    score = 100.0
    if monotony >= 85:
        score -= 30
    elif monotony >= 70:
        score -= 18
    elif monotony >= 55:
        score -= 8

    if pause_ratio < 0.07:
        score -= 18
    elif pause_ratio < 0.11:
        score -= 10
    elif pause_ratio > 0.45:
        score -= 8

    if energy < 0.012:
        score -= 14
    elif energy < 0.018:
        score -= 8
    elif energy > 0.11:
        score -= 10

    return int(max(20, min(100, round(score))))


def _grade(score: int) -> str:
    if score >= 90: return "Excellent"
    if score >= 75: return "Good"
    if score >= 55: return "Needs Work"
    if score >= 35: return "Poor"
    return "Critical"


def _overall_event_penalty(state: "SessionState") -> float:
    """
    Session-level penalty derived from interruption severity.

    Why this exists:
    - Dimension scores capture *where* issues happened.
    - Overall score should also reflect *how often* the coach had to step in.
    """
    if not state.events:
        return 0.0

    raw = 0.0
    for event in state.events:
        raw += _EVENT_SEVERITY.get(getattr(event, "event_type", ""), 2.0)

    # Cap prevents very long sessions from collapsing to zero purely from count.
    return min(25.0, raw)
