"""
PitchMirror — helpers for the meeting/practice extras: talk time, live commitments,
past-call memory, pre-call rehearsal, practice rewrites, progress trends and goals.

Pure functions (prompts, parsers, small calculations) so they are easy to test;
backend/main.py wires them into the live session, the post-session pipeline and the API.
"""
from __future__ import annotations

import re
import time

# ── Talk time ────────────────────────────────────────────────────────────────
# Seconds of uninterrupted talking before a "check in" nudge, per meeting type.
# Presentations are meant to be one long turn, so they get no monologue nudge.
MONOLOGUE_LIMIT_S = {"sales_demo": 90, "general": 90, "pitch": 120, "interview": 120}
# Talk-share nudge (You vs Them over the last 5 minutes) only fits two-way conversations.
TALK_SHARE_MODES = ("sales_demo", "general")
TALK_SHARE_LIMIT = 0.75
TALK_SHARE_CUE = "You're doing most of the talking — ask a question and listen."
MONOLOGUE_CUE = "Long answer — pause and ask them a question."


def monologue_limit_s(mode: str) -> int | None:
    return MONOLOGUE_LIMIT_S.get(mode)


def talk_summary(you_s: float, them_s: float) -> dict:
    total = max(0.0, you_s) + max(0.0, them_s)
    share = round(you_s / total, 3) if total >= 30 and them_s > 0 else None
    return {"you_s": round(max(0.0, you_s), 1), "them_s": round(max(0.0, them_s), 1), "you_share": share}


# ── Live commitments ─────────────────────────────────────────────────────────
# Cheap first pass on every finished line; the fast model then confirms and rewrites it.
PROMISE_RE = re.compile(
    r"\b(i'll|i will|i'm going to|i am going to|we'll|we will|we're going to|we are going to|"
    r"i promise|i can (?:send|share|get|set up|schedule)|"
    r"let me (?:send|share|get|check|follow|find|circle|loop|email|set up|schedule|confirm)|"
    r"you'll (?:get|have|receive)|expect (?:an?|the) (?:email|deck|proposal|quote|invite))\b",
    re.IGNORECASE,
)
MAX_COMMITMENTS = 30


def looks_like_commitment(text: str) -> bool:
    return len((text or "").split()) >= 4 and bool(PROMISE_RE.search(text or ""))


def commitment_prompt(speaker: str, line: str, context: str) -> str:
    who = "You (the presenter)" if speaker == "user" else "Them (the other side)"
    return (
        "Live meeting. Recent conversation (You = presenter, Them = the other side):\n"
        f"{context}\n\n"
        f"Latest line, said by {who}:\n\"{line}\"\n\n"
        "Did that latest line contain a concrete commitment to do something for the other side "
        "(send, share, schedule, check, deliver, follow up)? Ignore vague intentions like 'I'll explain'.\n"
        "Reply with one line per commitment: COMMIT: <task, max 12 words, imperative> | <when, or ->\n"
        "or exactly: NONE"
    )


def parse_commitments(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*commit\s*:\s*(.+)", line, re.IGNORECASE)
        if not m:
            continue
        parts = [p.strip() for p in m.group(1).split("|")]
        task = " ".join(parts[0].split())[:120]
        due = parts[1] if len(parts) > 1 else ""
        if due in {"-", "–", "—", "none", "n/a"}:
            due = ""
        if task:
            out.append({"task": task, "due": due[:40]})
    return out[:3]


def fallback_commitment(line: str) -> dict:
    """Without a text model: keep the sentence that holds the promise."""
    sentences = re.split(r"(?<=[.!?])\s+", line.strip())
    hit = next((s for s in sentences if PROMISE_RE.search(s)), line.strip())
    return {"task": " ".join(hit.split())[:120], "due": ""}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def is_duplicate_commitment(task: str, existing: list[dict]) -> bool:
    words = set(_norm(task).split())
    if not words:
        return True
    for item in existing:
        other = set(_norm(item.get("task", "")).split())
        if other and len(words & other) / max(1, min(len(words), len(other))) >= 0.7:
            return True
    return False


def commitments_text(items: list[dict]) -> str:
    lines = []
    for c in items:
        who = "You" if c.get("owner") == "you" else "Them"
        due = f" (by {c['due']})" if c.get("due") else ""
        lines.append(f"- [{who}] {c['task']}{due}")
    return "\n".join(lines)


# ── Past-call memory ─────────────────────────────────────────────────────────
def normalize_client(name: str) -> str:
    return " ".join(re.sub(r"[^\w&.' -]+", " ", name or "").split())[:80]


def client_key(name: str) -> str:
    return normalize_client(name).lower()


def previous_call_summary(payload: dict) -> dict:
    """Compact view of a saved meeting for the next call with the same client."""
    recap = payload.get("recap") or {}
    commitments = [c for c in payload.get("commitments") or [] if isinstance(c, dict)]
    promised = [c["task"] for c in commitments if c.get("owner") == "you" and c.get("task")]
    for item in recap.get("action_items") or []:
        if isinstance(item, dict) and str(item.get("owner", "")).lower() in {"you", "me", ""} and item.get("task"):
            if not any(_norm(item["task"]) == _norm(p) for p in promised):
                promised.append(item["task"])
    return {
        "session_id": payload.get("session_id"),
        "created_at": payload.get("created_at"),
        "client": payload.get("client", ""),
        "coach_mode": payload.get("coach_mode", ""),
        "summary": recap.get("summary", ""),
        "open_questions": list(recap.get("open_questions") or [])[:6],
        "objections": list(recap.get("client_signals") or [])[:6],
        "promised": promised[:6],
        "their_commitments": [c["task"] for c in commitments if c.get("owner") == "them" and c.get("task")][:4],
    }


def previous_call_context(prev: dict, *, max_chars: int = 1200) -> str:
    if not prev or not (prev.get("summary") or prev.get("open_questions") or prev.get("promised")):
        return ""
    when = time.strftime("%d %b %Y", time.localtime(int(prev.get("created_at") or 0))) if prev.get("created_at") else ""
    parts = [f"Last call with {prev.get('client') or 'this client'}{f' ({when})' if when else ''}: {prev.get('summary', '')}"]
    if prev.get("open_questions"):
        parts.append("Still open from last time: " + "; ".join(prev["open_questions"]))
    if prev.get("objections"):
        parts.append("Their concerns last time: " + "; ".join(prev["objections"]))
    if prev.get("promised"):
        parts.append("You promised: " + "; ".join(prev["promised"]))
    return "\n".join(parts)[:max_chars]


# ── Pre-call rehearsal ───────────────────────────────────────────────────────
REHEARSAL_QUESTIONS = 3
_MODE_LABEL = {
    "interview": "job interview", "pitch": "investor pitch", "sales_demo": "sales call / demo",
    "presentation": "presentation", "general": "meeting",
}


def rehearsal_questions_prompt(mode: str, client: str, notes: str, points: list[str], previous: str) -> str:
    label = _MODE_LABEL.get(mode, "meeting")
    brief = []
    if client:
        brief.append(f"Other side: {client}")
    if notes:
        brief.append(f"Presenter's notes: {notes[:3000]}")
    if points:
        brief.append("Points the presenter plans to cover: " + "; ".join(points[:8]))
    if previous:
        brief.append(previous)
    return (
        f"You are preparing a presenter for an upcoming {label}.\n"
        + ("\n".join(brief) if brief else "No brief was given — use typical questions for this kind of meeting.")
        + f"\n\nPlay the other side. Write the {REHEARSAL_QUESTIONS} hardest questions they are likely to ask — "
        "specific to this brief, the kind that expose weak spots (price, proof, risk, timing, competition, "
        "gaps in experience). Spoken style, one sentence each.\n"
        "Reply with exactly one line per question: Q: <question> | <why it's hard, max 10 words>"
    )


def parse_rehearsal_questions(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*(?:q\d*|\d+[.)])\s*[:.)-]?\s*(.+)", line, re.IGNORECASE)
        if not m:
            continue
        parts = [p.strip() for p in m.group(1).split("|")]
        q = " ".join(parts[0].split()).strip('"“”')
        if len(q.split()) >= 4:
            out.append({"question": q[:300], "why": (parts[1] if len(parts) > 1 else "")[:100]})
    return out[:REHEARSAL_QUESTIONS]


FALLBACK_QUESTIONS = {
    "interview": [
        "Tell me about a time a project you led failed — what did you do?",
        "Why should we pick you over the other candidates?",
        "What would you change in your first 90 days here?",
    ],
    "pitch": [
        "Why will you win against the big players already in this market?",
        "What evidence do you have that customers will pay for this?",
        "How will you use this money, and what does it get you to?",
    ],
    "sales_demo": [
        "Why is this worth the price compared with what we use today?",
        "How long does setup really take, and who on our side has to do the work?",
        "What happens if it doesn't deliver the results you're promising?",
    ],
}


def fallback_questions(mode: str) -> list[dict]:
    qs = FALLBACK_QUESTIONS.get(mode) or FALLBACK_QUESTIONS["sales_demo"]
    return [{"question": q, "why": ""} for q in qs]


# ── Practice rewrites ("say it better") ──────────────────────────────────────
def rewrite_input(transcript: list[dict], *, max_chars: int = 6000) -> str:
    lines = [f"[{int(seg.get('ts', 0))}s] {seg['t'].strip()}" for seg in transcript
             if seg.get("s") == "u" and (seg.get("t") or "").strip()]
    return "\n".join(lines)[-max_chars:]


def parse_rewrites(raw: str) -> list[dict]:
    import json
    text = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        original = " ".join(str(item.get("original") or "").split())
        rewrite = " ".join(str(item.get("rewrite") or "").split())
        if original and rewrite:
            try:
                ts = max(0.0, float(item.get("ts") or 0))
            except (TypeError, ValueError):
                ts = 0.0
            out.append({"original": original[:800], "rewrite": rewrite[:800],
                        "why": " ".join(str(item.get("why") or "").split())[:200], "ts": ts})
    return out[:3]


# ── Progress trends and goals ────────────────────────────────────────────────
GOAL_METRICS = {
    # metric: (label, unit, direction) — "max": lower is better, "min": higher is better
    "fillers_per_min": ("Fillers per minute", "/min", "max"),
    "wpm": ("Speaking pace", "wpm", "max"),
    "eye_drops_per_min": ("Look-aways per minute", "/min", "max"),
    "score": ("Session score", "/100", "min"),
    "sessions_per_week": ("Practice sessions", "per week", "min"),
}
MAX_GOALS = 6


def session_trend_point(payload: dict) -> dict:
    """Per-session numbers for the progress charts."""
    categories = payload.get("categories") or {}
    duration = float(payload.get("duration_seconds") or 0)
    words = sum(len(str(seg.get("t") or "").split()) for seg in payload.get("transcript") or []
                if isinstance(seg, dict) and seg.get("s") == "u")
    talk = payload.get("talk_stats") or {}
    speaking_s = float(talk.get("you_s") or 0) if payload.get("session_kind") == "meeting" else 0.0
    minutes = (speaking_s if speaking_s >= 20 else duration) / 60
    fillers = (categories.get("filler_words") or {}).get("count")
    drops = (categories.get("eye_contact") or {}).get("drops")
    ok = minutes >= 0.25 and not payload.get("insufficient_speech")
    return {
        "session_id": payload.get("session_id"),
        "created_at": payload.get("created_at"),
        "session_kind": payload.get("session_kind", "practice"),
        "score": payload.get("overall_score") if ok else None,
        "fillers_per_min": round(fillers / minutes, 2) if ok and isinstance(fillers, (int, float)) else None,
        "wpm": round(words / minutes) if ok and words else None,
        "eye_drops_per_min": round(drops / (duration / 60), 2) if ok and isinstance(drops, (int, float)) and duration else None,
        "duration_seconds": duration,
    }


def clean_goals(raw) -> list[dict]:
    out, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        metric = str(item.get("metric") or "")
        if metric not in GOAL_METRICS or metric in seen:
            continue
        try:
            target = float(item.get("target"))
        except (TypeError, ValueError):
            continue
        if not (0 <= target <= 1000):
            continue
        seen.add(metric)
        out.append({"metric": metric, "target": round(target, 2),
                    "created_at": int(item.get("created_at") or time.time())})
    return out[:MAX_GOALS]
