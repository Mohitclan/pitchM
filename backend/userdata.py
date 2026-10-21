"""
Pitchlight — each person's saved scripts and uploaded documents.

Stored in the same SQLite file as sessions and accounts (db.LOCAL_DB_PATH). Every read and write is scoped to a
user id, so one person can never see another's scripts or documents.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from backend import library, script_follow

MAX_SCRIPTS = 40
COMPANY_ID = "__company__"          # docs owned by this id are the shared company knowledge hub
COMPANY_MAX_DOCS = 300
COMPANY_MAX_TOTAL_CHARS = 3_000_000


def _s(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_script_payload(payload: dict) -> dict:
    """Validate and trim a script before saving. Raises ValueError with a message the person can read."""
    name = _s(payload.get("name"), 80)
    if not name:
        raise ValueError("Give the script a name.")
    text = str(payload.get("text") or "").replace("\r", "")[:20_000]
    sections = script_follow.parse_script_text(text)
    if not sections:
        raise ValueError("The script is empty. Add at least one line under a ## heading.")
    key_points = [_s(p, 120) for p in (payload.get("key_points") or []) if _s(p, 120)][:10]
    hard = []
    for item in payload.get("hard_questions") or []:
        if isinstance(item, dict) and _s(item.get("question"), 300):
            hard.append({"question": _s(item["question"], 300), "answer": _s(item.get("answer"), 900)})
    objections = []
    for item in payload.get("objections") or []:
        if isinstance(item, dict) and _s(item.get("label"), 40) and _s(item.get("answer"), 400):
            objections.append({"label": _s(item["label"], 40), "answer": _s(item["answer"], 400)})
    try:
        minutes = max(0, min(240, int(payload.get("minutes") or 0)))
    except (TypeError, ValueError):
        minutes = 0
    mode = str(payload.get("mode") or "sales_demo")
    return {
        "name": name,
        "mode": mode if mode in ("sales_demo", "pitch", "interview", "presentation", "general") else "sales_demo",
        "client": _s(payload.get("client"), 80),
        "minutes": minutes,
        "text": script_follow.format_script(sections),          # normalised: ## headings, one line per row
        "key_points": key_points,
        "hard_questions": hard[:12],
        "objections": objections[:12],
        "cheat_sheet": [_s(c, 200) for c in (payload.get("cheat_sheet") or []) if _s(c, 200)][:12],
    }


class UserDataStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scripts (
                script_id  TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                name       TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                payload    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_scripts_user ON scripts (user_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS docs (
                doc_id     TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                title      TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                size       INTEGER NOT NULL,
                text       TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_docs_user ON docs (user_id, created_at DESC);
            """
        )
        self._conn.execute("""CREATE TABLE IF NOT EXISTS ventures (
            name TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL)""")
        if "venture" not in {r[1] for r in self._conn.execute("PRAGMA table_info(docs)")}:
            self._conn.execute("ALTER TABLE docs ADD COLUMN venture TEXT NOT NULL DEFAULT ''")
        self._conn.commit()

    def _run(self, sql: str, params: tuple = (), *, write: bool = False) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            if write:
                self._conn.commit()
            return rows

    # ── scripts ─────────────────────────────────────────────────────────────
    def list_scripts(self, user_id: str) -> list[dict]:
        out = []
        for sid, name, updated, payload in self._run(
                "SELECT script_id, name, updated_at, payload FROM scripts WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)):
            data = json.loads(payload)
            sections = script_follow.parse_script_text(data.get("text", ""))
            out.append({"script_id": sid, "name": name, "updated_at": updated, "minutes": data.get("minutes", 0),
                        "mode": data.get("mode", "sales_demo"), "lines": sum(len(s["lines"]) for s in sections),
                        "sections": len(sections)})
        return out

    def get_script(self, user_id: str, script_id: str) -> dict | None:
        rows = self._run("SELECT script_id, payload, updated_at FROM scripts WHERE script_id = ? AND user_id = ?", (script_id, user_id))
        if not rows:
            return None
        data = json.loads(rows[0][1])
        return {**data, "script_id": rows[0][0], "updated_at": rows[0][2],
                "sections": script_follow.parse_script_text(data.get("text", ""))}

    def save_script(self, user_id: str, payload: dict, script_id: str | None = None) -> dict:
        clean = clean_script_payload(payload)
        now = int(time.time())
        if script_id:
            if not self.get_script(user_id, script_id):
                raise KeyError("script not found")
            self._run("UPDATE scripts SET name = ?, updated_at = ?, payload = ? WHERE script_id = ? AND user_id = ?",
                      (clean["name"], now, json.dumps(clean), script_id, user_id), write=True)
        else:
            if len(self.list_scripts(user_id)) >= MAX_SCRIPTS:
                raise ValueError(f"You can keep up to {MAX_SCRIPTS} scripts. Delete one first.")
            script_id = f"s_{secrets.token_hex(6)}"
            self._run("INSERT INTO scripts (script_id, user_id, name, created_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?)",
                      (script_id, user_id, clean["name"], now, now, json.dumps(clean)), write=True)
        return self.get_script(user_id, script_id)

    def delete_script(self, user_id: str, script_id: str) -> bool:
        before = len(self._run("SELECT 1 FROM scripts WHERE script_id = ? AND user_id = ?", (script_id, user_id)))
        self._run("DELETE FROM scripts WHERE script_id = ? AND user_id = ?", (script_id, user_id), write=True)
        return bool(before)

    # ── documents ───────────────────────────────────────────────────────────
    def list_docs(self, user_id: str) -> list[dict]:
        return [{"doc_id": d, "title": t, "created_at": c, "size": n, "venture": v}
                for d, t, c, n, v in self._run(
                    "SELECT doc_id, title, created_at, size, venture FROM docs WHERE user_id = ? ORDER BY venture, created_at DESC", (user_id,))]

    def add_doc(self, user_id: str, title: str, text: str, venture: str = "") -> dict:
        title = _s(title, 80) or "Untitled document"
        venture = _s(venture, 60)
        company = user_id == COMPANY_ID
        text = library.clean_text(text)[: (150_000 if company else library.MAX_DOC_CHARS)]
        if len(text) < 20:
            raise ValueError("That document is empty or too short.")
        docs = self.list_docs(user_id)
        if len(docs) >= (COMPANY_MAX_DOCS if company else library.MAX_DOCS):
            raise ValueError("The library is full. Delete a document first." if company
                             else f"You can keep up to {library.MAX_DOCS} documents. Delete one first.")
        if sum(d["size"] for d in docs) + len(text) > (COMPANY_MAX_TOTAL_CHARS if company else library.MAX_TOTAL_CHARS):
            raise ValueError("The library is over its size limit. Delete a document first." if company
                             else "Your documents are over the size limit. Delete one first.")
        doc_id = f"d_{secrets.token_hex(6)}"
        now = int(time.time())
        self._run("INSERT INTO docs (doc_id, user_id, title, created_at, size, text, venture) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (doc_id, user_id, title, now, len(text), text, venture), write=True)
        return {"doc_id": doc_id, "title": title, "created_at": now, "size": len(text), "venture": venture}

    def get_docs(self, user_id: str, doc_ids: list[str] | None = None) -> list[dict]:
        rows = self._run("SELECT doc_id, title, text, venture FROM docs WHERE user_id = ? ORDER BY created_at", (user_id,))
        return [{"doc_id": d, "title": t, "text": x, "venture": v} for d, t, x, v in rows if doc_ids is None or d in doc_ids]

    # ── company businesses (ventures / products) ────────────────────────────
    def list_ventures(self) -> list[dict]:
        """Every business with its documents. Businesses that only exist as a document label are included."""
        docs = self.list_docs(COMPANY_ID)
        rows = {n: {"name": n, "description": d, "docs": []} for n, d in self._run(
            "SELECT name, description FROM ventures ORDER BY name COLLATE NOCASE")}
        for d in docs:
            name = d["venture"] or "General"
            rows.setdefault(name, {"name": name, "description": "", "docs": []})["docs"].append(d)
        return sorted(rows.values(), key=lambda v: (v["name"] == "General", v["name"].lower()))

    def add_venture(self, name: str, description: str = "") -> dict:
        name, description = _s(name, 60), _s(description, 1500)
        if not name:
            raise ValueError("Give the business a name.")
        if name.lower() == "general":
            raise ValueError("\"General\" is reserved for documents that belong to no business.")
        if any(v["name"].lower() == name.lower() for v in self.list_ventures()):
            raise ValueError("That business already exists.")
        self._run("INSERT INTO ventures (name, description, created_at) VALUES (?, ?, ?)", (name, description, int(time.time())), write=True)
        return {"name": name, "description": description, "docs": []}

    def update_venture(self, name: str, description: str) -> dict:
        if not self._run("SELECT 1 FROM ventures WHERE name = ?", (name,)):
            self._run("INSERT INTO ventures (name, description, created_at) VALUES (?, '', ?)", (name, int(time.time())), write=True)
        self._run("UPDATE ventures SET description = ? WHERE name = ?", (_s(description, 1500), name), write=True)
        return next(v for v in self.list_ventures() if v["name"] == name)

    def delete_venture(self, name: str) -> int:
        """Remove a business and all of its documents; returns how many documents went."""
        n = len(self._run("SELECT 1 FROM docs WHERE user_id = ? AND venture = ?", (COMPANY_ID, name)))
        self._run("DELETE FROM docs WHERE user_id = ? AND venture = ?", (COMPANY_ID, name), write=True)
        self._run("DELETE FROM ventures WHERE name = ?", (name,), write=True)
        return n

    def company_version(self) -> tuple:
        """Changes whenever the company library changes (used to cache its search index)."""
        row = self._run("SELECT COUNT(*), COALESCE(SUM(size), 0), COALESCE(MAX(created_at), 0) FROM docs WHERE user_id = ?", (COMPANY_ID,))[0]
        v = self._run("SELECT COUNT(*), COALESCE(SUM(LENGTH(name) + LENGTH(description)), 0) FROM ventures")[0]
        return tuple(row) + tuple(v)

    def venture_summaries(self) -> list[dict]:
        return [{"name": n, "description": d} for n, d in self._run("SELECT name, description FROM ventures")]

    def delete_doc(self, user_id: str, doc_id: str) -> bool:
        before = len(self._run("SELECT 1 FROM docs WHERE doc_id = ? AND user_id = ?", (doc_id, user_id)))
        self._run("DELETE FROM docs WHERE doc_id = ? AND user_id = ?", (doc_id, user_id), write=True)
        return bool(before)


_store: UserDataStore | None = None
_store_path: str | None = None


def get_userdata() -> UserDataStore:
    global _store, _store_path
    from backend import db
    path = db.LOCAL_DB_PATH or "data/pitchmirror.db"
    if _store is None or _store_path != path:
        _store, _store_path = UserDataStore(path), path
    return _store
