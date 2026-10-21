"""
Pitchlight — "Prepare me": turn the material someone already has (a product page, a deck's text, an FAQ,
notes about the client) into everything they need for a sales or client call: a script they can follow,
the key points, the hardest questions with answers, objection answers and a one-page cheat sheet.

The draft may only use facts that are in the material. Missing facts become "[add: …]" placeholders,
never invented numbers.
"""
from __future__ import annotations

import json
import re

from backend import script_follow

MAX_MATERIAL_CHARS = 24_000

SYSTEM = """You prepare a salesperson for a client call. You write what they will SAY, in plain spoken language.

Hard rules:
- Use ONLY facts that appear in MATERIAL. Never invent numbers, prices, customer names, features, dates or results.
- If an important fact is missing, write a placeholder like [add: price per clinic] where it belongs, and list it
  in "missing".
- Spoken style: short sentences, at most 20 words per line, no jargon, no filler, no marketing clichés.
- Output ONLY one JSON object, no markdown fences, no commentary."""

SCHEMA = """{
  "name": "<short name for this script, e.g. 'Northwind Dental call'>",
  "sections": [
    {"title": "Opening", "lines": ["<3-4 spoken lines: thank them, say what you'll cover, check it suits them>"]},
    {"title": "Their situation", "lines": ["<3-4 questions to ask THEM about how they work today and what hurts>"]},
    {"title": "What we do", "lines": ["<3-5 lines: the product in their terms, tied to their situation>"]},
    {"title": "Proof", "lines": ["<2-4 lines: evidence, numbers or examples from the material>"]},
    {"title": "Price and fit", "lines": ["<2-3 lines: price or how it's priced, what's included>"]},
    {"title": "Next step", "lines": ["<2-3 lines: a specific ask with a date, e.g. a pilot or a follow-up call>"]}
  ],
  "key_points": ["<4-6 things they must leave knowing, each at most 10 words>"],
  "hard_questions": [{"question": "<a hard question a client is likely to ask>", "answer": "<2-3 spoken sentences using only the material>"}],
  "objections": [{"label": "Price", "answer": "<one spoken sentence>"}, {"label": "Competitor", "answer": "..."},
                 {"label": "Timing", "answer": "..."}, {"label": "Authority", "answer": "..."}],
  "cheat_sheet": ["<6-8 key facts and numbers straight from the material, one short line each>"],
  "missing": ["<facts that would make this better but are not in the material>"]
}"""


def build_prompt(material: str, *, client: str = "", goal: str = "", minutes: int = 0, language: str = "English") -> str:
    parts = ["MATERIAL (the only source of facts):\n---\n" + material[:MAX_MATERIAL_CHARS].strip() + "\n---"]
    ctx = []
    if client:
        ctx.append(f"The client: {client}")
    if goal:
        ctx.append(f"The goal of the call: {goal}")
    if minutes:
        ctx.append(f"Planned length: {minutes} minutes (size the script to fit)")
    ctx.append(f"Write the script and answers in {language}.")
    parts.append("\n".join(ctx))
    parts.append("Return exactly this JSON shape (fill every field; keep the section titles unless the material clearly needs others):\n" + SCHEMA)
    return "\n\n".join(parts)


def _text(v, limit: int) -> str:
    return " ".join(str(v or "").split())[:limit]


def parse_prepared(raw: str) -> dict | None:
    """The model's JSON → a script payload (see userdata.clean_script_payload). None if it isn't usable."""
    text = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    sections = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        lines = [_text(ln, 240) for ln in sec.get("lines") or [] if _text(ln, 240)]
        if lines:
            sections.append({"title": _text(sec.get("title"), 60) or "Section", "lines": lines})
    if not sections:
        return None
    hard = [{"question": _text(q.get("question"), 300), "answer": _text(q.get("answer"), 900)}
            for q in data.get("hard_questions") or [] if isinstance(q, dict) and _text(q.get("question"), 300)]
    objections = [{"label": _text(o.get("label"), 40), "answer": _text(o.get("answer"), 400)}
                  for o in data.get("objections") or [] if isinstance(o, dict) and _text(o.get("label"), 40) and _text(o.get("answer"), 400)]
    return {
        "name": _text(data.get("name"), 80) or "My call script",
        "text": script_follow.format_script(sections),
        "key_points": [_text(p, 120) for p in data.get("key_points") or [] if _text(p, 120)][:8],
        "hard_questions": hard[:10],
        "objections": objections[:8],
        "cheat_sheet": [_text(c, 200) for c in data.get("cheat_sheet") or [] if _text(c, 200)][:10],
        "missing": [_text(m, 160) for m in data.get("missing") or [] if _text(m, 160)][:8],
    }


def objections_text(objections: list[dict]) -> str:
    """The brief's "Topic: answer" format."""
    return "\n".join(f"{o['label']}: {o['answer']}" for o in objections)
