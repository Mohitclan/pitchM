"""
PitchMirror — pre-call brief for Meeting Mode.

Before a meeting the presenter can paste context (client, agenda, notes) and/or the
key points they must cover. During the meeting the fast Claude model checks which
points have been covered so far; near the planned end the bubble reminds the presenter
of anything still open. The brief is also given to the live helpers (answer outlines,
conversation checks) and to the after-call recap.
"""
from __future__ import annotations

import re

MAX_POINTS = 8
MAX_NOTES_CHARS = 6000
REMINDER_AT_FRACTION = 0.8      # remind at 80% of the planned meeting length


def clean_points(raw_points) -> list[str]:
    """Normalise user-entered points: strip bullets/numbers, drop empties and duplicates."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_points or []:
        text = re.sub(r"^\s*(?:[-*•▪◦]|\d+[.)])\s*", "", str(item or "")).strip()
        text = " ".join(text.split())[:160]
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= MAX_POINTS:
            break
    return out


def new_point_rows(points: list[str]) -> list[dict]:
    return [{"text": p, "covered": False, "ts": None} for p in points]


def extract_points_prompt(notes: str, meeting_type: str) -> str:
    return (
        f"These are a presenter's notes for an upcoming {meeting_type.replace('_', ' ')}:\n"
        f"---\n{notes[:MAX_NOTES_CHARS]}\n---\n"
        f"List the {MAX_POINTS} or fewer most important points the presenter must cover or get across "
        "in the meeting (key messages, asks, questions to raise). One per line, each at most 10 words, "
        "no numbering, no commentary."
    )


def parse_points_reply(text: str) -> list[str]:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return clean_points(lines)


def coverage_prompt(points: list[dict], conversation: str) -> str:
    listing = "\n".join(f"{i + 1}. {p['text']}" for i, p in enumerate(points))
    return (
        "A presenter planned to cover these points in a live meeting:\n"
        f"{listing}\n\n"
        "Conversation so far (You = presenter, Them = other participants):\n"
        f"{conversation or '(nothing yet)'}\n\n"
        "Which point numbers has the PRESENTER (You) clearly covered? Mentioning the topic counts only if "
        "the substance was actually said. Reply with exactly one line: COVERED: <comma-separated numbers> "
        "— or — COVERED: NONE."
    )


def parse_coverage_reply(text: str, n_points: int) -> set[int] | None:
    """'COVERED: 1, 3' → {0, 2} (0-based). None when the reply isn't in that form."""
    m = re.search(r"covered\s*:\s*(.*)", text or "", re.IGNORECASE)
    if not m:
        return None
    rest = m.group(1)
    if re.match(r"\s*none\b", rest, re.IGNORECASE):
        return set()
    nums = {int(x) - 1 for x in re.findall(r"\d+", rest)}
    return {i for i in nums if 0 <= i < n_points}


def brief_context(notes: str, points: list[dict], *, max_chars: int = 1200) -> str:
    """Short brief block for other prompts (answer outlines, checks, recap)."""
    parts = []
    if notes:
        parts.append(f"Presenter's notes: {notes[:max_chars]}")
    if points:
        parts.append("Points to cover: " + "; ".join(
            f"{p['text']}{' (done)' if p['covered'] else ''}" for p in points
        ))
    return "\n".join(parts)
