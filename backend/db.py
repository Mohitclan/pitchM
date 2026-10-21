"""
Pitchlight — session / user-profile storage.
Single module-level instance shared across all sessions — no per-session init churn.

Storage backends, in priority order:
  1. Firestore      — when GOOGLE_CLOUD_PROJECT is set (cloud deployment).
  2. Local SQLite   — otherwise, at LOCAL_DB_PATH (default ./data/pitchmirror.db).
                      In Docker the data/ dir is a named volume, so sessions and
                      user profiles survive restarts, rebuilds and `docker compose down`.
  3. In-memory only — if SQLite can't be opened (or LOCAL_DB_PATH is set empty).
"""
import asyncio
from collections import deque
from copy import deepcopy
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

FIRESTORE_COLLECTION = os.getenv("FIRESTORE_COLLECTION", "pitchmirror_sessions")
PROFILES_COLLECTION = os.getenv("PROFILES_COLLECTION", "pitchmirror_user_profiles")
FEEDBACK_COLLECTION = os.getenv("FEEDBACK_COLLECTION", "pitchmirror_feedback")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default=%d", name, raw, default)
        return default


RECENT_SESSION_CACHE_SIZE = max(10, _int_env("RECENT_SESSION_CACHE_SIZE", 200))
LOCAL_DB_PATH = os.getenv("LOCAL_DB_PATH", "data/pitchmirror.db").strip()


class LocalStore:
    """Small SQLite-backed store for session scorecards and user skill profiles."""

    def __init__(self, path: str):
        self.path = path
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id    TEXT,
                created_at INTEGER,
                payload    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user_created
                ON sessions (user_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id    TEXT PRIMARY KEY,
                updated_at INTEGER,
                payload    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                session_id TEXT PRIMARY KEY,
                user_id    TEXT,
                created_at INTEGER,
                payload    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS goals (
                user_id    TEXT PRIMARY KEY,
                updated_at INTEGER,
                payload    TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def _run(self, sql: str, params: tuple = (), *, write: bool = False) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            if write:
                self._conn.commit()
            return rows

    def save_session(self, session_id: str, payload: dict) -> None:
        self._run(
            "INSERT OR REPLACE INTO sessions (session_id, user_id, created_at, payload) VALUES (?, ?, ?, ?)",
            (session_id, payload.get("user_id"), int(payload.get("created_at") or 0),
             json.dumps(payload, default=str)),
            write=True,
        )

    def get_session(self, session_id: str) -> Optional[dict]:
        rows = self._run("SELECT payload FROM sessions WHERE session_id = ?", (session_id,))
        return json.loads(rows[0][0]) if rows else None

    def list_recent(self, limit: int, user_id: Optional[str]) -> list[dict]:
        if user_id:
            rows = self._run(
                "SELECT payload FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
        else:
            rows = self._run("SELECT payload FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,))
        return [json.loads(r[0]) for r in rows]

    def save_profile(self, user_id: str, payload: dict) -> None:
        self._run(
            "INSERT OR REPLACE INTO user_profiles (user_id, updated_at, payload) VALUES (?, ?, ?)",
            (user_id, int(payload.get("updated_at") or 0), json.dumps(payload, default=str)),
            write=True,
        )

    def save_feedback(self, session_id: str, payload: dict) -> None:
        self._run(
            "INSERT OR REPLACE INTO feedback (session_id, user_id, created_at, payload) VALUES (?, ?, ?, ?)",
            (session_id, payload.get("user_id"), int(payload.get("created_at") or 0), json.dumps(payload, default=str)),
            write=True,
        )

    def list_feedback(self, limit: int) -> list[dict]:
        rows = self._run("SELECT payload FROM feedback ORDER BY created_at DESC LIMIT ?", (limit,))
        return [json.loads(r[0]) for r in rows]

    def get_profile(self, user_id: str) -> Optional[dict]:
        rows = self._run("SELECT payload FROM user_profiles WHERE user_id = ?", (user_id,))
        return json.loads(rows[0][0]) if rows else None

    def save_goals(self, user_id: str, payload: dict) -> None:
        self._run(
            "INSERT OR REPLACE INTO goals (user_id, updated_at, payload) VALUES (?, ?, ?)",
            (user_id, int(payload.get("updated_at") or 0), json.dumps(payload, default=str)),
            write=True,
        )

    def get_goals(self, user_id: str) -> Optional[dict]:
        rows = self._run("SELECT payload FROM goals WHERE user_id = ?", (user_id,))
        return json.loads(rows[0][0]) if rows else None


class FirestoreClient:
    def __init__(self):
        self._client = None
        self._firestore = None
        self._enabled = False
        self._memory_store: dict[str, dict] = {}
        self._recent_ids: deque[str] = deque()
        self._user_profiles: dict[str, dict] = {}
        self._feedback: dict[str, dict] = {}
        self._goals: dict[str, dict] = {}
        self._local: Optional[LocalStore] = None
        self._init()

    def _init(self):
        try:
            from google.cloud import firestore
            project = os.getenv("GOOGLE_CLOUD_PROJECT")
            if project:
                self._client = firestore.AsyncClient(project=project)
                self._firestore = firestore
                self._enabled = True
                logger.info("Firestore client initialized")
            else:
                logger.warning("GOOGLE_CLOUD_PROJECT not set — Firestore disabled")
        except ImportError:
            logger.warning("google-cloud-firestore not installed — Firestore disabled")
        except Exception as e:
            logger.warning(f"Firestore init failed: {e}")

        if not self._enabled and LOCAL_DB_PATH:
            try:
                self._local = LocalStore(LOCAL_DB_PATH)
                logger.info("Local SQLite storage enabled at %s", LOCAL_DB_PATH)
            except Exception as e:
                logger.warning("Local SQLite init failed (%s) — running in-memory only", e)

    @property
    def durable(self) -> bool:
        """True when data survives a restart (Firestore or local SQLite)."""
        return self._enabled or self._local is not None

    def _cache_session(self, session_id: str, payload: dict) -> None:
        self._memory_store[session_id] = deepcopy(payload)
        try:
            self._recent_ids.remove(session_id)
        except ValueError:
            pass
        self._recent_ids.append(session_id)

        while len(self._recent_ids) > RECENT_SESSION_CACHE_SIZE:
            evicted = self._recent_ids.popleft()
            self._memory_store.pop(evicted, None)

    @staticmethod
    def _sanitize_for_persistence(payload: dict) -> dict:
        """
        Keep scorecard documents small and Firestore-friendly.

        Post-session visuals are returned live to the browser already; storing
        base64 binaries in Firestore inflates document size and can break writes.
        """
        sanitized = deepcopy(payload)
        assets = sanitized.get("generated_assets")
        if isinstance(assets, list):
            compact_assets: list[dict] = []
            for asset in assets[:2]:
                if not isinstance(asset, dict):
                    continue
                item = dict(asset)
                if "data_base64" in item:
                    item.pop("data_base64", None)
                    item["data_omitted"] = True
                compact_assets.append(item)
            sanitized["generated_assets"] = compact_assets
        return sanitized

    async def save_session(self, session_id: str, scorecard: dict) -> bool:
        payload = dict(scorecard)
        payload.setdefault("session_id", session_id)
        payload.setdefault("created_at", int(time.time()))
        payload_to_store = self._sanitize_for_persistence(payload)

        self._cache_session(session_id, payload_to_store)

        if not self._enabled or self._client is None:
            if self._local is not None:
                try:
                    await asyncio.to_thread(self._local.save_session, session_id, payload_to_store)
                    logger.info("Saved session %s to local SQLite", session_id)
                    return True
                except Exception as e:
                    logger.error("Failed to save session %s locally: %s", session_id, e)
                    return False
            logger.debug(
                "[in-memory] session %s score=%s (no durable storage — lost on restart)",
                session_id, payload_to_store.get("overall_score"),
            )
            return False
        try:
            doc_ref = self._client.collection(FIRESTORE_COLLECTION).document(session_id)
            await doc_ref.set(payload_to_store)
            logger.info(f"Saved session {session_id} to Firestore")
            return True
        except Exception as e:
            logger.error(f"Failed to save session {session_id}: {e}")
            return False

    async def get_session(self, session_id: str) -> Optional[dict]:
        if self._enabled and self._client is not None:
            try:
                doc_ref = self._client.collection(FIRESTORE_COLLECTION).document(session_id)
                doc = await doc_ref.get()
                if doc.exists:
                    payload = doc.to_dict() or {}
                    self._cache_session(session_id, payload)
                    return payload
            except Exception as e:
                logger.error(f"Failed to fetch session {session_id}: {e}")

        if self._local is not None:
            try:
                payload = await asyncio.to_thread(self._local.get_session, session_id)
                if payload:
                    self._cache_session(session_id, payload)
                    return payload
            except Exception as e:
                logger.error("Failed to read session %s locally: %s", session_id, e)

        cached = self._memory_store.get(session_id)
        return deepcopy(cached) if cached else None

    async def save_user_profile(self, user_id: str, profile: dict) -> bool:
        """Persist a structured skill profile keyed by user_id (cross-session memory)."""
        payload = dict(profile)
        payload["user_id"] = user_id
        payload["updated_at"] = int(time.time())
        self._user_profiles[user_id] = deepcopy(payload)

        if not self._enabled or self._client is None:
            if self._local is not None:
                try:
                    await asyncio.to_thread(self._local.save_profile, user_id, payload)
                    logger.info("User profile saved to local SQLite for %s", user_id)
                    return True
                except Exception as e:
                    logger.error("Failed to save user profile locally for %s: %s", user_id, e)
                    return False
            logger.debug("[in-memory] user profile saved for %s (no durable storage)", user_id)
            return False
        try:
            doc_ref = self._client.collection(PROFILES_COLLECTION).document(user_id)
            await doc_ref.set(payload)
            logger.info("User profile saved to Firestore for %s", user_id)
            return True
        except Exception as e:
            logger.error("Failed to save user profile for %s: %s", user_id, e)
            return False

    async def get_user_profile(self, user_id: str) -> Optional[dict]:
        """Retrieve the latest skill profile for a user, or None for first-time users."""
        if self._enabled and self._client is not None:
            try:
                doc_ref = self._client.collection(PROFILES_COLLECTION).document(user_id)
                doc = await doc_ref.get()
                if doc.exists:
                    payload = doc.to_dict() or {}
                    self._user_profiles[user_id] = payload
                    return deepcopy(payload)
            except Exception as e:
                logger.error("Failed to fetch user profile for %s: %s", user_id, e)

        if self._local is not None:
            try:
                payload = await asyncio.to_thread(self._local.get_profile, user_id)
                if payload:
                    self._user_profiles[user_id] = payload
                    return deepcopy(payload)
            except Exception as e:
                logger.error("Failed to read user profile locally for %s: %s", user_id, e)

        cached = self._user_profiles.get(user_id)
        return deepcopy(cached) if cached else None

    async def save_feedback(self, session_id: str, feedback: dict) -> bool:
        """Pilot feedback for one session (one entry per session; resubmitting replaces it)."""
        payload = dict(feedback)
        payload["session_id"] = session_id
        payload.setdefault("created_at", int(time.time()))
        self._feedback[session_id] = deepcopy(payload)
        if self._enabled and self._client is not None:
            try:
                await self._client.collection(FEEDBACK_COLLECTION).document(session_id).set(payload)
                return True
            except Exception as e:
                logger.error("Failed to save feedback for %s: %s", session_id, e)
                return False
        if self._local is not None:
            try:
                await asyncio.to_thread(self._local.save_feedback, session_id, payload)
                return True
            except Exception as e:
                logger.error("Failed to save feedback locally for %s: %s", session_id, e)
        return False

    async def list_feedback(self, limit: int = 500) -> list[dict]:
        if self._enabled and self._client is not None and self._firestore is not None:
            try:
                query = (
                    self._client.collection(FEEDBACK_COLLECTION)
                    .order_by("created_at", direction=self._firestore.Query.DESCENDING)
                    .limit(limit)
                )
                return [doc.to_dict() or {} async for doc in query.stream()]
            except Exception as e:
                logger.error("Failed to list feedback: %s", e)
        if self._local is not None:
            try:
                return await asyncio.to_thread(self._local.list_feedback, limit)
            except Exception as e:
                logger.error("Failed to list feedback locally: %s", e)
        rows = sorted(self._feedback.values(), key=lambda r: r.get("created_at", 0), reverse=True)
        return [deepcopy(r) for r in rows[:limit]]

    async def save_goals(self, user_id: str, goals: list[dict]) -> bool:
        """Practice goals for one user (local SQLite or memory; not mirrored to Firestore)."""
        payload = {"user_id": user_id, "goals": goals, "updated_at": int(time.time())}
        self._goals[user_id] = deepcopy(payload)
        if self._local is not None:
            try:
                await asyncio.to_thread(self._local.save_goals, user_id, payload)
                return True
            except Exception as e:
                logger.error("Failed to save goals for %s: %s", user_id, e)
        return False

    async def get_goals(self, user_id: str) -> list[dict]:
        if self._local is not None:
            try:
                payload = await asyncio.to_thread(self._local.get_goals, user_id)
                if payload:
                    return list(payload.get("goals") or [])
            except Exception as e:
                logger.error("Failed to read goals for %s: %s", user_id, e)
        return list((self._goals.get(user_id) or {}).get("goals") or [])

    async def list_recent(self, limit: int = 20, user_id: Optional[str] = None) -> list[dict]:
        limit = max(1, min(limit, 100))

        if self._enabled and self._client is not None and self._firestore is not None:
            try:
                collection = self._client.collection(FIRESTORE_COLLECTION)
                if user_id:
                    query = (
                        collection.where("user_id", "==", user_id)
                        .order_by("created_at", direction=self._firestore.Query.DESCENDING)
                        .limit(limit)
                    )
                else:
                    query = (
                        collection.order_by("created_at", direction=self._firestore.Query.DESCENDING)
                        .limit(limit)
                    )
                docs = [doc async for doc in query.stream()]
                rows = [doc.to_dict() or {} for doc in docs]
                for row in rows:
                    sid = row.get("session_id")
                    if sid:
                        self._cache_session(sid, row)
                return rows
            except Exception as e:
                logger.error(f"Failed to list recent sessions from Firestore: {e}")

        if self._local is not None:
            try:
                return await asyncio.to_thread(self._local.list_recent, limit, user_id)
            except Exception as e:
                logger.error("Failed to list recent sessions locally: %s", e)

        recent: list[dict] = []
        for sid in reversed(self._recent_ids):
            row = self._memory_store.get(sid)
            if row and (not user_id or row.get("user_id") == user_id):
                recent.append(deepcopy(row))
            if len(recent) >= limit:
                break
        return recent


# Module-level singleton — initialized once at import time, reused across all sessions
_db = FirestoreClient()


def get_db() -> FirestoreClient:
    return _db
