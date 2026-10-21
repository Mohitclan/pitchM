"""
Pitchlight — the script that follows you.

A script is a few named sections ("## Opening", "## What we do" …), each a handful of short spoken lines.
While you speak, the follower matches your words to the script's lines (so paraphrasing is fine) and tells
the screen which line is next, what you already covered and which section you skipped.

Plain text format (easy to edit anywhere):

    ## Opening
    Thanks for making the time, Priya.
    Today I'd like to show how Billing Hub centralises payments.

    ## What we do
    ...
"""
from __future__ import annotations

import math
import re
import time

from backend.library import content_tokens

MAX_LINES = 160
MAX_SECTIONS = 14
LONG_LINE_WORDS = 28       # longer lines are split into sentences so each one can be followed


def _split_long(line: str) -> list[str]:
    if len(line.split()) <= LONG_LINE_WORDS:
        return [line]
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", line) if p.strip()]
    return parts or [line]


def parse_script_text(text: str) -> list[dict]:
    """'## Title' starts a section; every other non-empty line is a script line."""
    sections: list[dict] = []
    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^#{1,3}\s*(.+)$", line)
        if m:
            sections.append({"title": " ".join(m.group(1).split())[:60], "lines": []})
            continue
        line = re.sub(r"^[-*•]\s+", "", line)
        if not sections:
            sections.append({"title": "Script", "lines": []})
        sections[-1]["lines"].extend(_split_long(" ".join(line.split())[:400]))
    sections = [s for s in sections if s["lines"]][:MAX_SECTIONS]
    total = 0
    for s in sections:
        s["lines"] = s["lines"][: max(0, MAX_LINES - total)]
        total += len(s["lines"])
    return [s for s in sections if s["lines"]]


def format_script(sections: list[dict]) -> str:
    return "\n\n".join(f"## {s['title']}\n" + "\n".join(s["lines"]) for s in sections if s.get("lines"))


def flatten(sections: list[dict]) -> list[dict]:
    out, idx = [], 0
    for si, s in enumerate(sections):
        for text in s["lines"]:
            out.append({"idx": idx, "section": si, "text": text})
            idx += 1
    return out


class ScriptFollower:
    """Tracks the presenter's place in the script from what they say."""

    NEAR_AHEAD = 10             # lines ahead of the current place that count as "near"
    TAIL_TOKENS = 10            # words kept from the previous segment (a sentence can arrive in two pieces)
    SHORT_SEGMENT = 12          # only segments shorter than this borrow the previous segment's tail

    def __init__(self, sections: list[dict]):
        self.sections = sections
        self.lines = []
        for ln in flatten(sections):
            toks = set(content_tokens(ln["text"]))
            self.lines.append({**ln, "toks": toks})
        df: dict[str, int] = {}
        for ln in self.lines:
            for t in ln["toks"]:
                df[t] = df.get(t, 0) + 1
        n = max(1, len(self.lines))
        self._w = {t: 1.0 + math.log(n / c) for t, c in df.items()}    # words found in many lines count for less
        self.pos = -1                     # index of the last line spoken
        self.covered: set[int] = set()
        self._tail: list[str] = []
        self.last_match_at = 0.0          # monotonic time of the last match

    def __bool__(self) -> bool:
        return bool(self.lines)

    # ── matching ────────────────────────────────────────────────────────────
    def _score(self, line_toks: set, window: set) -> tuple[float, int]:
        if not line_toks:
            return 0.0, 0
        hit = line_toks & window
        total = sum(self._w.get(t, 1.0) for t in line_toks)
        return (sum(self._w.get(t, 1.0) for t in hit) / total if total else 0.0), len(hit)

    def _is_match(self, frac: float, shared: int, n_tokens: int, near: bool) -> bool:
        if n_tokens <= 4:                                  # short lines: nearly all their words must be there
            return shared >= 2 and frac >= (0.75 if near else 0.9)
        if near:
            return frac >= 0.45 and shared >= 3
        return frac >= 0.65 and shared >= 4

    def feed(self, text: str) -> bool:
        """Take newly spoken words. Returns True when the place or the covered set changed."""
        tokens = content_tokens(text)
        if not tokens or not self.lines:
            return False
        window = set(tokens) | (set(self._tail) if len(tokens) < self.SHORT_SEGMENT else set())
        self._tail = tokens[-self.TAIL_TOKENS:]
        lo, hi = max(0, self.pos - 1), min(len(self.lines), self.pos + 1 + self.NEAR_AHEAD)
        matched: list[int] = []
        for ln in self.lines:
            frac, shared = self._score(ln["toks"], window)
            if self._is_match(frac, shared, len(ln["toks"]), near=lo <= ln["idx"] < hi):
                matched.append(ln["idx"])
        if not matched:
            return False
        before = (self.pos, len(self.covered))
        self.covered.update(matched)
        newest = max(matched)
        if newest > self.pos or (matched and max(matched) < self.pos - 2):
            self.pos = newest                      # moved on (or went back and re-read an earlier part)
        self.last_match_at = time.monotonic()
        return (self.pos, len(self.covered)) != before

    def locate(self, text: str) -> tuple[int, float]:
        """Best line for some recent words anywhere in the script: (index, confidence 0-1); (-1, 0) if none.
        When several lines fit about equally well (the words span a few lines), the LATEST one is the place."""
        window = set(content_tokens(text))
        scored = []
        for ln in self.lines:
            frac, shared = self._score(ln["toks"], window)
            if shared >= 2 and frac > 0:
                scored.append((frac, ln["idx"]))
        if not scored:
            return -1, 0.0
        best = max(f for f, _ in scored)
        idx = max(i for f, i in scored if f >= best - 0.1)
        return idx, round(best, 2)

    def goto(self, next_idx: int) -> None:
        """Presenter says "I'm about to say line N": the place is the line before it."""
        next_idx = max(0, min(len(self.lines) - 1, int(next_idx)))
        self.pos = next_idx - 1
        self.last_match_at = time.monotonic()

    def set_position(self, last_spoken_idx: int) -> None:
        self.pos = max(-1, min(len(self.lines) - 1, int(last_spoken_idx)))
        if self.pos >= 0:
            self.covered.add(self.pos)
        self.last_match_at = time.monotonic()

    # ── what the screen needs ──────────────────────────────────────────────
    def next_index(self) -> int | None:
        nxt = self.pos + 1
        return nxt if nxt < len(self.lines) else None

    def section_of(self, idx: int) -> int:
        return self.lines[max(0, min(len(self.lines) - 1, idx))]["section"] if self.lines else 0

    def skipped_sections(self) -> list[int]:
        """Sections passed over: a later section has been spoken but this one is mostly uncovered."""
        if not self.covered:
            return []
        latest = max(self.section_of(i) for i in self.covered)
        out = []
        for si, sec in enumerate(self.sections):
            idxs = [ln["idx"] for ln in self.lines if ln["section"] == si]
            done = sum(1 for i in idxs if i in self.covered)
            if si < latest and idxs and done / len(idxs) < 0.3:
                out.append(si)
        return out

    def status(self) -> dict:
        nxt = self.next_index()
        total = len(self.lines)
        return {
            "pos": self.pos,
            "next": nxt,
            "section": self.section_of(nxt if nxt is not None else self.pos),
            "covered": sorted(self.covered),
            "skipped": self.skipped_sections(),
            "total": total,
            "pct": round(100 * len(self.covered) / total) if total else 0,
        }

    def where_you_are(self, lookahead: int = 2) -> dict:
        """For the "I'm lost" answer: the last line spoken, and the next lines to say."""
        here = self.lines[self.pos]["text"] if 0 <= self.pos < len(self.lines) else ""
        nxt = self.next_index()
        upcoming = [self.lines[i]["text"] for i in range(nxt, min(len(self.lines), nxt + lookahead))] if nxt is not None else []
        sec = self.sections[self.section_of(nxt if nxt is not None else self.pos)]["title"] if self.sections else ""
        return {"here": here, "next": upcoming, "section": sec, "pos": self.pos, "finished": nxt is None and self.pos >= 0}

    def section_coverage(self) -> list[dict]:
        out = []
        for si, sec in enumerate(self.sections):
            idxs = [ln["idx"] for ln in self.lines if ln["section"] == si]
            out.append({"title": sec["title"], "covered": sum(1 for i in idxs if i in self.covered), "total": len(idxs)})
        return out
