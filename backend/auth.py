"""
Pitchlight — simple built-in accounts (no outside sign-in service).

Each person creates an account (name, a username or company email, password) and signs in. Everything they do —
practice sessions, meetings, history, goals, pitches, clients — is stored under their own user id and
only they can read it.

Switch it on with AUTH_MODE=accounts. With AUTH_MODE=off (the default) the app keeps the old
"Who's testing?" picker, so a single-laptop pilot works unchanged.

Settings (all optional):
  AUTH_MODE=accounts            turn accounts on
  ALLOWED_EMAIL_DOMAINS=panscience.ai   comma list; when set, people must sign up with an email in these
                                domains. When empty, a plain username (or any email) is fine — handy for local testing
  ADMIN_EMAILS=a@x.com,b@x.com  these people can open the pilot-feedback summary; if empty, the first
                                account ever created becomes the admin
  SESSION_DAYS=30               how long a sign-in lasts
  COOKIE_SECURE=true            force the Secure cookie flag (it is also set automatically behind HTTPS)

Command line (inside the container):
  python -m backend.auth list
  python -m backend.auth reset-password someone@company.com
  python -m backend.auth make-admin someone@company.com
  python -m backend.auth adopt-data OLD_USER_ID someone@company.com   # attach pre-accounts history
"""
from __future__ import annotations

import asyncio
import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

logger = logging.getLogger(__name__)

COOKIE_NAME = "pl_session"
MIN_PASSWORD_LEN = 8
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


def accounts_enabled() -> bool:
    return os.getenv("AUTH_MODE", "off").strip().lower() == "accounts"


def _csv_env(name: str) -> list[str]:
    return [x.strip().lower() for x in os.getenv(name, "").split(",") if x.strip()]


def allowed_domains() -> list[str]:
    return [d.lstrip("@") for d in _csv_env("ALLOWED_EMAIL_DOMAINS")]


def admin_emails() -> list[str]:
    return _csv_env("ADMIN_EMAILS")


def session_days() -> int:
    try:
        return max(1, int(os.getenv("SESSION_DAYS", "30")))
    except ValueError:
        return 30


# ── passwords ────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got.hex(), digest)
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── storage (same SQLite file as the sessions) ───────────────────────────────
class AuthStore:
    def __init__(self, path: str):
        self.path = path
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id    TEXT PRIMARY KEY,
                email      TEXT UNIQUE NOT NULL,
                name       TEXT NOT NULL,
                pw_hash    TEXT NOT NULL,
                role       TEXT NOT NULL DEFAULT 'member',
                created_at INTEGER NOT NULL,
                last_login INTEGER
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions (user_id);
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

    @staticmethod
    def _user(row: tuple | None) -> dict | None:
        if not row:
            return None
        return {"user_id": row[0], "email": row[1], "name": row[2], "role": row[3], "created_at": row[4],
                "last_login": row[5]}

    _COLS = "user_id, email, name, role, created_at, last_login"

    def count_users(self) -> int:
        return self._run("SELECT COUNT(*) FROM users")[0][0]

    def get_user(self, user_id: str) -> dict | None:
        return self._user(next(iter(self._run(f"SELECT {self._COLS} FROM users WHERE user_id = ?", (user_id,))), None))

    def get_user_by_email(self, email: str) -> dict | None:
        return self._user(next(iter(self._run(f"SELECT {self._COLS} FROM users WHERE email = ?", (email.lower(),))), None))

    def list_users(self) -> list[dict]:
        return [self._user(r) for r in self._run(f"SELECT {self._COLS} FROM users ORDER BY created_at")]

    def create_user(self, email: str, name: str, password: str, role: str = "member") -> dict:
        user_id = f"u_{secrets.token_hex(6)}"
        self._run(
            "INSERT INTO users (user_id, email, name, pw_hash, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, email.lower(), name, hash_password(password), role, int(time.time())), write=True,
        )
        return self.get_user(user_id)

    def verify_login(self, email: str, password: str) -> dict | None:
        rows = self._run("SELECT pw_hash, user_id FROM users WHERE email = ?", (email.lower(),))
        if not rows:
            # Spend the same time as a real check so timing doesn't reveal which emails exist.
            verify_password(password, hash_password("not-a-real-password"))
            return None
        if not verify_password(password, rows[0][0]):
            return None
        self._run("UPDATE users SET last_login = ? WHERE user_id = ?", (int(time.time()), rows[0][1]), write=True)
        return self.get_user(rows[0][1])

    def set_password(self, user_id: str, password: str) -> None:
        self._run("UPDATE users SET pw_hash = ? WHERE user_id = ?", (hash_password(password), user_id), write=True)
        self._run("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,), write=True)   # sign out everywhere

    def set_role(self, user_id: str, role: str) -> None:
        self._run("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id), write=True)

    def create_session(self, user_id: str, days: int | None = None) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        self._run("INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                  (_token_hash(token), user_id, now, now + (days or session_days()) * 86400), write=True)
        return token

    def user_for_token(self, token: str | None) -> dict | None:
        if not token:
            return None
        rows = self._run("SELECT user_id, expires_at FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))
        if not rows:
            return None
        if rows[0][1] < time.time():
            self._run("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),), write=True)
            return None
        return self.get_user(rows[0][0])

    def delete_session(self, token: str | None) -> None:
        if token:
            self._run("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),), write=True)

    def adopt_data(self, old_user_id: str, new_user_id: str) -> dict:
        """Attach history saved before accounts existed to an account (sessions, profile, goals, feedback)."""
        counts = {}
        with self._lock:
            for table in ("sessions", "feedback"):
                rows = self._conn.execute(f"SELECT rowid, payload FROM {table} WHERE user_id = ?", (old_user_id,)).fetchall()
                for rowid, payload in rows:
                    try:
                        data = json.loads(payload)
                        data["user_id"] = new_user_id
                        payload = json.dumps(data, default=str)
                    except ValueError:
                        pass
                    self._conn.execute(f"UPDATE {table} SET user_id = ?, payload = ? WHERE rowid = ?",
                                       (new_user_id, payload, rowid))
                counts[table] = len(rows)
            for table in ("user_profiles", "goals"):
                try:
                    rows = self._conn.execute(f"SELECT payload FROM {table} WHERE user_id = ?", (old_user_id,)).fetchall()
                    if rows:
                        data = json.loads(rows[0][0])
                        data["user_id"] = new_user_id
                        self._conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (new_user_id,))
                        self._conn.execute(f"UPDATE {table} SET user_id = ?, payload = ? WHERE user_id = ?",
                                           (new_user_id, json.dumps(data, default=str), old_user_id))
                    counts[table] = len(rows)
                except sqlite3.OperationalError:
                    counts[table] = 0      # table not created yet
            self._conn.commit()
        return counts


_store: AuthStore | None = None
_store_path: str | None = None


def get_store() -> AuthStore:
    """The account store, bound to the same SQLite file as the sessions (db.LOCAL_DB_PATH)."""
    global _store, _store_path
    from backend import db
    path = db.LOCAL_DB_PATH or "data/pitchmirror.db"
    if _store is None or _store_path != path:
        _store, _store_path = AuthStore(path), path
    return _store


# ── sign-in attempts: a few wrong passwords in a row slows an account down ────
_failures: dict[str, list[float]] = {}
_MAX_FAILURES, _FAILURE_WINDOW_S = 6, 600.0


def _recent_failures(key: str) -> list[float]:
    cutoff = time.time() - _FAILURE_WINDOW_S
    _failures[key] = [t for t in _failures.get(key, []) if t >= cutoff]
    return _failures[key]


# ── request helpers ──────────────────────────────────────────────────────────
def user_from_request(request) -> dict | None:
    """The signed-in user for an HTTP request or WebSocket (reads the session cookie)."""
    return get_store().user_for_token(request.cookies.get(COOKIE_NAME))


def is_admin(user: dict | None) -> bool:
    return bool(user and user.get("role") == "admin")


def _cookie_secure(request: Request) -> bool:
    if os.getenv("COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip() == "https"


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=session_days() * 86400, httponly=True, samesite="lax",
                        secure=_cookie_secure(request), path="/")


def public_user(user: dict) -> dict:
    return {"user_id": user["user_id"], "name": user["name"], "email": user["email"],
            "username": user["email"], "role": user["role"]}


def normalize_identifier(raw: str) -> tuple[str, str | None]:
    """The sign-in name: lower-cased username or email. Returns (identifier, error message or None)."""
    ident = str(raw or "").strip().lower()
    domains = allowed_domains()
    if domains:
        if not _EMAIL_RE.match(ident) or len(ident) > 120:
            return ident, f"Use your company email ({', '.join('@' + d for d in domains)})."
        if ident.rsplit("@", 1)[1] not in domains:
            return ident, f"Use your company email ({', '.join('@' + d for d in domains)})."
        return ident, None
    if _EMAIL_RE.match(ident) and len(ident) <= 120:
        return ident, None
    if _USERNAME_RE.match(ident):
        return ident, None
    return ident, "Choose a username of 3–30 letters, numbers, dots, dashes or underscores (or use your email)."


# ── routes ───────────────────────────────────────────────────────────────────
router = APIRouter()


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid request")
    return body


@router.get("/auth/me")
async def auth_me(request: Request):
    """Always public: tells the pages whether accounts are on and who is signed in."""
    if not accounts_enabled():
        return {"mode": "off", "user": None}
    user = user_from_request(request)
    return {"mode": "accounts", "user": public_user(user) if user else None,
            "domains": allowed_domains()}


@router.post("/auth/register")
async def auth_register(request: Request):
    if not accounts_enabled():
        raise HTTPException(status_code=404, detail="Accounts are not enabled")
    body = await _json(request)
    name = " ".join(str(body.get("name") or "").split())[:60]
    password = str(body.get("password") or "")
    email, problem = normalize_identifier(body.get("username") or body.get("email"))
    if not name:
        raise HTTPException(status_code=400, detail="Please enter your name.")
    if problem:
        raise HTTPException(status_code=403 if allowed_domains() and _EMAIL_RE.match(email) else 400, detail=problem)
    if len(password) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    store = get_store()
    if store.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="That username is already taken. Sign in, or choose another.")
    admins = admin_emails()
    role = "admin" if (email in admins or (not admins and store.count_users() == 0)) else "member"
    user = await asyncio.to_thread(store.create_user, email, name, password, role)
    logger.info("account_created user=%s role=%s", user["user_id"], role)
    response = Response(content=json.dumps({"user": public_user(user)}), media_type="application/json")
    _set_cookie(response, request, store.create_session(user["user_id"]))
    return response


@router.post("/auth/login")
async def auth_login(request: Request):
    if not accounts_enabled():
        raise HTTPException(status_code=404, detail="Accounts are not enabled")
    body = await _json(request)
    email = str(body.get("username") or body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    key = email
    if len(_recent_failures(key)) >= _MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many wrong passwords. Wait 10 minutes and try again.")
    store = get_store()
    user = await asyncio.to_thread(store.verify_login, email, password)
    if not user:
        _failures.setdefault(key, []).append(time.time())
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    _failures.pop(key, None)
    if user["email"] in admin_emails() and user["role"] != "admin":
        store.set_role(user["user_id"], "admin")
        user = store.get_user(user["user_id"])
    response = Response(content=json.dumps({"user": public_user(user)}), media_type="application/json")
    _set_cookie(response, request, store.create_session(user["user_id"]))
    return response


@router.post("/auth/logout")
async def auth_logout(request: Request):
    get_store().delete_session(request.cookies.get(COOKIE_NAME))
    response = Response(content=json.dumps({"ok": True}), media_type="application/json")
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


# ── command line ─────────────────────────────────────────────────────────────
def _cli(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"list", "reset-password", "make-admin", "adopt-data"}:
        print(__doc__)
        return 2
    store = get_store()
    cmd = argv[1]
    if cmd == "list":
        for u in store.list_users():
            last = time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_login"])) if u["last_login"] else "never"
            print(f"{u['user_id']}  {u['role']:6}  {u['email']:36}  {u['name']}  (last sign-in: {last})")
        print(f"{store.count_users()} account(s)")
        return 0
    if len(argv) < 3:
        print("Missing email.")
        return 2
    user = store.get_user_by_email(argv[2])
    if not user:
        print(f"No account for {argv[2]}")
        return 1
    if cmd == "make-admin":
        store.set_role(user["user_id"], "admin")
        print(f"{user['email']} is now an admin.")
    elif cmd == "reset-password":
        pw = getpass.getpass("New password: ")
        if len(pw) < MIN_PASSWORD_LEN or pw != getpass.getpass("Repeat: "):
            print(f"Passwords must match and be at least {MIN_PASSWORD_LEN} characters.")
            return 1
        store.set_password(user["user_id"], pw)
        print("Password changed; they were signed out everywhere.")
    elif cmd == "adopt-data":
        print("Usage: adopt-data OLD_USER_ID EMAIL") if len(argv) < 4 else None
        if len(argv) >= 4:
            user = store.get_user_by_email(argv[3])
            if not user:
                print(f"No account for {argv[3]}")
                return 1
            print("Moved:", store.adopt_data(argv[2], user["user_id"]), "->", user["email"])
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
