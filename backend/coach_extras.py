"""
Pitchlight — helpers for the meeting/practice extras: talk time, live commitments,
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
        "facts": [f["text"] for f in payload.get("facts") or [] if isinstance(f, dict) and f.get("text")][:8],
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
    if prev.get("facts"):
        parts.append("Facts from last time: " + "; ".join(prev["facts"]))
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


# ── Live facts card (meeting) ────────────────────────────────────────────────
# Names, numbers, dates, needs and current tools said in the call, so the presenter can
# reuse them ("Priya", "40 stores", "go live in March") and they reach the follow-up email.
FACT_KINDS = ("person", "number", "date", "need", "tool")
MAX_FACTS = 40
FACTS_CHECK_EVERY_S = 20.0
FACTS_MIN_NEW_WORDS = 12
# Cheap pre-check on the new lines: skip the model call when nothing fact-like was said.
FACT_HINT_RE = re.compile(
    r"\d|\$|€|£|₹|%|\b(?:hundred|thousand|million|billion|lakh|crore|percent|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?|monday|tuesday|wednesday|thursday|friday|weekend|week|month|quarter|year|"
    r"need|needs|want|wants|goal|problem|pain|budget|deadline|priority|team|using|use|currently|today|"
    r"name|i'm|i am|my name|head of|manager|director|ceo|cto|founder|vp)\b",
    re.IGNORECASE,
)


def facts_prompt(conversation: str, existing: list[dict]) -> str:
    known = "; ".join(f["text"] for f in existing[-25:]) or "(none yet)"
    return (
        "Live meeting. Recent conversation (You = presenter, Them = the other side):\n"
        f"{conversation}\n\n"
        f"Facts already noted: {known}\n\n"
        "List NEW concrete facts from this conversation that the presenter should remember and reuse: "
        "PERSON (a name, with role or company if said), NUMBER (a quantity, price, size or metric, with what it "
        "counts), DATE (a deadline, launch or meeting time, with what it is for), NEED (a goal, pain or "
        "requirement Them stated), TOOL (what Them uses today, or a competitor). Skip anything vague, "
        "already noted, or only said by You about your own product.\n"
        "One per line: FACT: <PERSON|NUMBER|DATE|NEED|TOOL> | <fact, max 12 words> | <You|Them>\n"
        "or exactly: NONE"
    )


def parse_facts(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*fact\s*:\s*(.+)", line, re.IGNORECASE)
        if not m:
            continue
        parts = [p.strip() for p in m.group(1).split("|")]
        if len(parts) < 2:
            continue
        kind = parts[0].lower()
        fact = " ".join(parts[1].split()).strip(" .")[:120]
        who = "you" if len(parts) > 2 and parts[2].strip().lower().startswith("you") else "them"
        if kind in FACT_KINDS and fact:
            out.append({"kind": kind, "text": fact, "who": who})
    return out[:6]


def facts_text(facts: list[dict]) -> str:
    return "\n".join(f"- {f['kind'].upper()}: {f['text']}" for f in facts)


# ── Pitch versions (practice) ────────────────────────────────────────────────
MAX_PITCH_POINTS = 10


def normalize_pitch(name: str) -> str:
    return " ".join(re.sub(r"[^\w&.'+ -]+", " ", name or "").split())[:80]


def pitch_key(name: str) -> str:
    return normalize_pitch(name).lower()


def pitch_version_row(payload: dict) -> dict:
    """One saved practice run of a named pitch, for the versions table."""
    pitch = payload.get("pitch") or {}
    points = [p for p in pitch.get("points") or [] if isinstance(p, dict)]
    trend = session_trend_point(payload)
    return {
        "session_id": payload.get("session_id"),
        "version": pitch.get("version"),
        "created_at": payload.get("created_at"),
        "score": None if payload.get("insufficient_speech") else payload.get("overall_score"),
        "duration_seconds": payload.get("duration_seconds"),
        "words": sum(len(str(seg.get("t") or "").split()) for seg in payload.get("transcript") or []
                     if isinstance(seg, dict) and seg.get("s") == "u"),
        "fillers_per_min": trend.get("fillers_per_min"),
        "wpm": trend.get("wpm"),
        "points_hit": sum(1 for p in points if p.get("covered")),
        "points_total": len(points),
        "points": points,
    }


def group_pitch_versions(sessions: list[dict]) -> list[dict]:
    """Saved sessions → [{name, versions: [oldest … newest]}], most recently practised first."""
    groups: dict[str, dict] = {}
    for payload in sessions:
        pitch = payload.get("pitch") or {}
        key = pitch_key(pitch.get("name", ""))
        if not key:
            continue
        g = groups.setdefault(key, {"name": pitch.get("name"), "versions": [], "last_at": 0})
        g["versions"].append(pitch_version_row(payload))
        g["last_at"] = max(g["last_at"], int(payload.get("created_at") or 0))
    out = []
    for g in groups.values():
        g["versions"].sort(key=lambda v: (v.get("created_at") or 0))
        out.append(g)
    out.sort(key=lambda g: g["last_at"], reverse=True)
    return out


# ── Role-play partner (practice) ─────────────────────────────────────────────
ROLEPLAY_ROLES = ("investor", "interviewer", "client")
ROLEPLAY_LABELS = {"investor": "Investor", "interviewer": "Interviewer", "client": "Client"}


# ── Live advice (meeting "next move" coach + practice advice cards) ─────────
CHATTINESS = ("quiet", "balanced", "active")
# Seconds between advice checks, and the minimum new words before one is worth asking.
ADVICE_EVERY_S = {"balanced": 30.0, "active": 18.0}
ADVICE_MIN_NEW_WORDS = 15
ADVICE_GAP_S = 12.0            # never show two pieces of advice closer than this
POSITIVE_GAP_S = 240.0         # positive notes stay rare
PRACTICE_ADVICE_EVERY_S = 20.0
PRACTICE_ADVICE_MIN_NEW_WORDS = 20
ADVICE_KINDS = ("next", "missed", "good", "tip")


def meeting_advice_prompt(mode: str, conversation: str, brief: str, recent: list[str], allow_good: bool) -> str:
    label = _MODE_LABEL.get(mode, "meeting")
    good = ("GOOD: <what the presenter just did well, max 9 words> — only if something was clearly strong.\n"
            if allow_good else "")
    return (
        f"You are a silent coach whispering to a presenter during a live {label}. Recent conversation "
        "(You = presenter, Them = the other side):\n"
        f"{conversation}\n\n"
        + (f"Context:\n{brief}\n\n" if brief else "")
        + (f"Advice already given (don't repeat): {'; '.join(recent[-5:])}\n\n" if recent else "")
        + "Give AT MOST ONE short piece of advice for right now, only if it clearly helps:\n"
        "MISSED: <the question Them asked that You didn't answer, max 10 words> — when You drifted away "
        "from a question Them asked.\n"
        "NEXT: <the best next move, imperative, max 10 words> — e.g. ask what happens if they don't fix "
        "it, confirm who else decides, ask about budget or timeline, bring up a point from the context "
        "not covered yet, check they're following.\n"
        + good
        + "Otherwise reply exactly: NONE\n"
        "Reply with one line in one of those forms and nothing else."
    )


def practice_advice_prompt(mode: str, transcript: str, points: list[dict], recent: list[str]) -> str:
    label = _MODE_LABEL.get(mode, "talk")
    open_points = [p["text"] for p in points if not p.get("covered")]
    return (
        f"You coach a presenter rehearsing a {label}. What they said in the last minute or so:\n"
        f"{transcript}\n\n"
        + (f"Key points they still need to cover: {'; '.join(open_points)}\n\n" if open_points else "")
        + (f"Advice already given (don't repeat): {'; '.join(recent[-5:])}\n\n" if recent else "")
        + "Give ONE short piece of content advice for what to do next (structure, clarity, evidence, "
        "missing point, ask, story) — or praise one thing done well. Not delivery (pace/fillers are "
        "measured separately).\n"
        "Reply with exactly one line: TIP: <advice, imperative, max 12 words> — or — "
        "GOOD: <what worked, max 10 words> — or — NONE"
    )


def parse_advice(text: str) -> tuple[str, str] | None:
    """'NEXT: Ask about budget' → ('next', 'Ask about budget'); None for NONE / anything else."""
    m = re.match(r"\s*(next|missed|good|tip)\s*[:—–-]\s*(.+)", (text or "").strip().splitlines()[0] if text else "",
                 re.IGNORECASE)
    if not m:
        return None
    advice = " ".join(m.group(2).split()).strip(" \"“”")
    if not advice or advice.upper().startswith("NONE"):
        return None
    return m.group(1).lower(), advice[:140]


def is_repeat_advice(text: str, recent: list[str]) -> bool:
    return is_duplicate_commitment(text, [{"task": r} for r in recent[-8:]])


# ── Objection cards (meeting) ────────────────────────────────────────────────
OBJECTION_KINDS = ("price", "competitor", "timing", "authority", "need", "trust", "other")
OBJECTION_RE = re.compile(
    r"\b(too (?:expensive|costly|much)|expensive|price|pricing|cost(?:s|ly)?|budget|afford|cheaper|discount|"
    r"already (?:use|have|using|work with)|we use|competitor|alternative|switch(?:ing)?|"
    r"not (?:now|right now|a priority|the right time|interested|sure)|next (?:quarter|year)|later|timing|"
    r"(?:talk|check|run it) (?:to|with|by) (?:my|our)|my boss|the board|decision|approve|"
    r"send me (?:an )?(?:email|deck|info)|think about it|don't (?:see|need)|why would (?:we|i)|"
    r"risk|security|data|trust|proof|guarantee|what if it (?:doesn't|fails))\b",
    re.IGNORECASE,
)
OBJECTION_GAP_S = 25.0


def parse_prepared_answers(text: str) -> list[dict]:
    """Brief lines 'Price: we pay back in 3 months' → [{label, answer}] (label is free text)."""
    out = []
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        label, answer = line.split(":", 1)
        label, answer = " ".join(label.split())[:40], " ".join(answer.split())[:300]
        if label and answer:
            out.append({"label": label, "answer": answer})
    return out[:12]


def objection_prompt(quote: str, conversation: str, prepared: list[dict], brief: str) -> str:
    listing = "\n".join(f"{i + 1}. {p['label']}: {p['answer']}" for i, p in enumerate(prepared)) or "(none)"
    return (
        "Live sales/business call. Recent conversation (You = presenter, Them = the other side):\n"
        f"{conversation}\n\n"
        + (f"Context:\n{brief}\n\n" if brief else "")
        + f"Them just said: \"{quote}\"\n\n"
        f"The presenter's prepared answers:\n{listing}\n\n"
        "Is this a real objection or push-back (price, competitor / current tool, timing, authority / "
        "needs approval, no need, trust / risk)? If not, reply exactly: NONE\n"
        "If yes, reply exactly in this form:\n"
        "OBJECTION: <price|competitor|timing|authority|need|trust|other> | <their concern in max 8 words>\n"
        "PREPARED: <number of the matching prepared answer, or 0>\n"
        "1. <first thing to say, max 12 words>\n2. <second, max 12 words>\n3. <question to ask back, max 12 words>"
    )


def parse_objection(text: str, prepared: list[dict]) -> dict | None:
    raw = text or ""
    m = re.search(r"objection\s*:\s*([a-z]+)\s*\|\s*(.+)", raw, re.IGNORECASE)
    if not m:
        return None
    kind = m.group(1).lower()
    kind = kind if kind in OBJECTION_KINDS else "other"
    concern = " ".join(m.group(2).split())[:80]
    pm = re.search(r"prepared\s*:\s*(\d+)", raw, re.IGNORECASE)
    idx = int(pm.group(1)) - 1 if pm else -1
    steps = [" ".join(s.split())[:100] for s in re.findall(r"^\s*\d[.)]\s*(.+)$", raw, re.MULTILINE)]
    steps = [s for s in steps if s][:3]
    card = {"kind": kind, "concern": concern, "points": steps,
            "prepared": prepared[idx] if 0 <= idx < len(prepared) else None}
    return card if (steps or card["prepared"]) else None


# ── Question coach + next-step guard (meeting) ───────────────────────────────
_OPEN_WORDS = frozenset({"what", "why", "how", "which", "when", "where", "who"})
_OPEN_STARTS = ("tell me", "walk me", "talk me", "describe", "help me understand", "explain")
QUESTION_COACH_MODES = ("sales_demo", "general")
QUESTION_COACH_AFTER_S = 300.0
NEXT_STEP_MODES = ("sales_demo", "general", "pitch")


def count_open_questions(text: str) -> int:
    """Open questions in a line: a '?' sentence with what/why/how/… in its first words, or 'tell me …'."""
    n = 0
    for sentence in re.split(r"(?<=[.!?])\s+", (text or "").strip()):
        low = sentence.lower().strip()
        words = re.findall(r"[a-z']+", low)[:5]
        if low.startswith(_OPEN_STARTS) or (low.endswith("?") and _OPEN_WORDS.intersection(words)):
            n += 1
    return n


def next_step_prompt(conversation: str) -> str:
    return (
        "Conversation so far in a business call (You = presenter, Them = the other side):\n"
        f"{conversation}\n\n"
        "Has a concrete next step been agreed — a follow-up meeting, trial, proposal or decision WITH a "
        "date or time? Reply exactly: YES or NO"
    )


# ── Practice: say the last thing better ──────────────────────────────────────
def say_better_prompt(mode: str, last: str) -> str:
    return (
        f"A presenter rehearsing a {_MODE_LABEL.get(mode, 'talk')} just said (speech-to-text, may have "
        f"small errors):\n\"{last}\"\n\n"
        "Rewrite it so it's stronger: clear point first, concrete, confident, spoken style, 1-3 short "
        "sentences, same facts — never invent numbers or names (use [placeholders]).\n"
        "Reply exactly: REWRITE: <the stronger version>"
    )


def parse_say_better(text: str) -> str:
    m = re.search(r"rewrite\s*:\s*(.+)", text or "", re.IGNORECASE | re.DOTALL)
    return " ".join((m.group(1) if m else "").split()).strip(" \"“”")[:500]
