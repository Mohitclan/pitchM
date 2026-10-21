"""Built-in accounts: sign-up / sign-in, per-person data, admin-only pages, live-connection sign-in."""
import asyncio

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A fresh app with accounts ON, its own database, and nothing signed in."""
    import backend.main as m
    from backend import auth, db
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("AUTH_MODE", "accounts")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAINS", "acme.com")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "acc.db"))
    monkeypatch.setattr(m, "AUTH_ACCOUNTS", True)
    store = db.FirestoreClient()
    monkeypatch.setattr(m, "_db", store)
    auth._failures.clear()
    auth._store = None
    return m, store


def _client(m):
    from fastapi.testclient import TestClient
    return TestClient(m.app, follow_redirects=False)


def _register(client, name, email, password="correct horse"):
    return client.post("/auth/register", json={"name": name, "email": email, "password": password})


def test_password_hashing_and_store(tmp_path):
    from backend import auth
    stored = auth.hash_password("s3cret-pass")
    assert stored.startswith("scrypt$") and "s3cret-pass" not in stored
    assert auth.verify_password("s3cret-pass", stored) and not auth.verify_password("wrong", stored)
    assert auth.hash_password("s3cret-pass") != stored                       # random salt each time
    st = auth.AuthStore(str(tmp_path / "s.db"))
    u = st.create_user("A@Acme.com", "Ann", "pw-12345678")
    assert u["email"] == "a@acme.com" and u["user_id"].startswith("u_")
    assert st.verify_login("a@acme.com", "pw-12345678")["user_id"] == u["user_id"]
    assert st.verify_login("a@acme.com", "nope") is None and st.verify_login("nobody@acme.com", "x") is None
    token = st.create_session(u["user_id"])
    assert st.user_for_token(token)["email"] == "a@acme.com"
    st.set_password(u["user_id"], "new-pass-1234")                            # also signs out everywhere
    assert st.user_for_token(token) is None and st.verify_login("a@acme.com", "new-pass-1234")


def test_register_login_logout_flow(env):
    m, _ = env
    c = _client(m)
    assert c.get("/auth/me").json() == {"mode": "accounts", "user": None, "domains": ["acme.com"]}
    assert _register(c, "Ann Lee", "ann@gmail.com").status_code == 403           # not a company email
    assert _register(c, "", "ann@acme.com").status_code == 400
    assert _register(c, "Ann Lee", "ann@acme.com", "short").status_code == 400
    ok = _register(c, "Ann Lee", "Ann@Acme.com")
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "admin"        # first account = admin
    assert "pl_session" in ok.headers["set-cookie"] and "HttpOnly" in ok.headers["set-cookie"]
    assert c.get("/auth/me").json()["user"]["email"] == "ann@acme.com"
    assert _register(_client(m), "Ann Again", "ann@acme.com").status_code == 409

    c2 = _client(m)
    assert c2.post("/auth/login", json={"email": "ann@acme.com", "password": "wrong"}).status_code == 401
    assert c2.post("/auth/login", json={"email": "ann@acme.com", "password": "correct horse"}).status_code == 200
    assert c2.get("/auth/me").json()["user"]["name"] == "Ann Lee"
    assert c2.post("/auth/logout").status_code == 200
    assert c2.get("/auth/me").json()["user"] is None
    assert _register(_client(m), "Bob Ray", "bob@acme.com").json()["user"]["role"] == "member"


def test_wrong_passwords_slow_an_account_down(env):
    m, _ = env
    _register(_client(m), "Ann Lee", "ann@acme.com")
    c = _client(m)
    codes = [c.post("/auth/login", json={"email": "ann@acme.com", "password": f"bad-{i}"}).status_code for i in range(8)]
    assert codes[:6] == [401] * 6 and codes[6:] == [429, 429]
    assert c.post("/auth/login", json={"email": "ann@acme.com", "password": "correct horse"}).status_code == 429


def test_pages_and_api_need_sign_in(env):
    m, _ = env
    c = _client(m)
    r = c.get("/app")
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/app"
    assert c.get("/meeting").status_code == 302 and c.get("/pilot").status_code == 302
    assert c.get("/api/sessions").status_code == 401
    assert c.get("/api/goals").status_code == 401
    for public in ("/", "/login", "/health", "/guide", "/api/config", "/auth/me", "/static/app.js"):
        assert c.get(public).status_code == 200, public
    _register(c, "Ann Lee", "ann@acme.com")
    assert c.get("/app").status_code == 200 and c.get("/api/sessions").status_code == 200
    assert c.get("/login").status_code == 302                                    # already signed in


def test_two_people_never_see_each_others_data(env):
    m, store = env
    ann, bob = _client(m), _client(m)
    ann_id = _register(ann, "Ann Lee", "ann@acme.com").json()["user"]["user_id"]
    bob_id = _register(bob, "Bob Ray", "bob@acme.com").json()["user"]["user_id"]
    asyncio.run(store.save_session("sess-ann-0001", {"user_id": ann_id, "created_at": 10, "session_kind": "meeting",
                                                       "client": "Acme Retail", "overall_score": 80}))
    asyncio.run(store.save_session("sess-bob-0001", {"user_id": bob_id, "created_at": 20, "session_kind": "practice",
                                                       "pitch": {"name": "Bob pitch", "version": 1, "points": []}}))
    assert [s["session_id"] for s in ann.get("/api/sessions").json()["sessions"]] == ["sess-ann-0001"]
    assert [s["session_id"] for s in bob.get("/api/sessions").json()["sessions"]] == ["sess-bob-0001"]
    assert ann.get("/api/sessions/sess-ann-0001").status_code == 200
    assert bob.get("/api/sessions/sess-ann-0001").status_code == 404
    # Claiming to be someone else (the old picker's header / query string) does nothing.
    assert bob.get("/api/sessions/sess-ann-0001", headers={"x-user-id": ann_id}).status_code == 404
    assert bob.get(f"/api/sessions?user={ann_id}").json()["sessions"][0]["session_id"] == "sess-bob-0001"
    assert [c["name"] for c in ann.get("/api/clients").json()["clients"]] == ["Acme Retail"]
    assert bob.get("/api/clients").json()["clients"] == []
    assert [p["name"] for p in bob.get("/api/pitches").json()["pitches"]] == ["Bob pitch"]
    assert ann.get("/api/pitches").json()["pitches"] == []
    # Goals are per person too.
    assert ann.put("/api/goals", json={"goals": [{"metric": "score", "target": 75}]}).status_code == 200
    assert bob.get("/api/goals").json()["goals"] == [] and len(ann.get("/api/goals").json()["goals"]) == 1


def test_feedback_summary_and_pilot_page_are_admin_only(env):
    m, _ = env
    admin, member = _client(m), _client(m)
    _register(admin, "Ann Lee", "ann@acme.com")
    _register(member, "Bob Ray", "bob@acme.com")
    assert admin.get("/api/feedback/summary").status_code == 200 and admin.get("/pilot").status_code == 200
    assert member.get("/api/feedback/summary").status_code == 403
    r = member.get("/pilot")
    assert r.status_code == 302 and r.headers["location"] == "/app"


def test_admin_emails_setting_decides_who_is_admin(env, monkeypatch):
    m, _ = env
    monkeypatch.setenv("ADMIN_EMAILS", "boss@acme.com")
    first = _register(_client(m), "Ann Lee", "ann@acme.com").json()["user"]
    boss = _register(_client(m), "The Boss", "boss@acme.com").json()["user"]
    assert first["role"] == "member" and boss["role"] == "admin"


def test_live_connections_need_sign_in(env):
    from starlette.websockets import WebSocketDisconnect
    m, _ = env
    c = _client(m)
    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect("/ws?kind=practice"):
            pass
    assert exc.value.code == 1008


def test_adopt_data_attaches_old_history_to_an_account(env):
    from backend import auth
    m, store = env
    uid = _register(_client(m), "Ann Lee", "ann@acme.com").json()["user"]["user_id"]
    asyncio.run(store.save_session("sess-old-0001", {"user_id": "user-9a47e525-fbb", "created_at": 5, "overall_score": 61}))
    asyncio.run(store.save_user_profile("user-9a47e525-fbb", {"next_focus": "pace"}))
    counts = auth.get_store().adopt_data("user-9a47e525-fbb", uid)
    assert counts["sessions"] == 1 and counts["user_profiles"] == 1
    fresh = type(store)()                                                     # new process, empty memory cache
    assert [s["session_id"] for s in asyncio.run(fresh.list_recent(10, uid))] == ["sess-old-0001"]
    assert asyncio.run(fresh.get_session("sess-old-0001"))["user_id"] == uid
    assert asyncio.run(fresh.get_user_profile(uid))["next_focus"] == "pace"


def test_accounts_off_keeps_the_old_behaviour(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "off.db"))
    monkeypatch.setattr(m, "AUTH_ACCOUNTS", False)
    monkeypatch.setattr(m, "_db", db.FirestoreClient())
    c = _client(m)
    assert c.get("/auth/me").json() == {"mode": "off", "user": None}
    assert c.post("/auth/register", json={"name": "A", "email": "a@x.com", "password": "12345678"}).status_code == 404
    assert c.get("/app").status_code == 200
    assert c.get("/api/sessions", headers={"x-user-id": "user-abc-123"}).status_code == 200


def test_usernames_work_when_no_company_domain_is_required(env, monkeypatch):
    m, _ = env
    monkeypatch.delenv("ALLOWED_EMAIL_DOMAINS", raising=False)                  # local testing: no domain rule
    assert _client(m).get("/auth/me").json()["domains"] == []
    me, friend = _client(m), _client(m)
    ok = _register(me, "Mansi", "Mansi_K")
    assert ok.status_code == 200 and ok.json()["user"]["username"] == "mansi_k"      # stored lower-case
    assert ok.json()["user"]["role"] == "admin"
    assert _register(friend, "Friend", "friend01").json()["user"]["role"] == "member"
    assert _register(_client(m), "Other", "MANSI_K").status_code == 409                # same name, any case
    assert _register(_client(m), "X", "ab").status_code == 400                         # too short
    assert _register(_client(m), "X", "has space").status_code == 400
    assert _register(_client(m), "X", "ok.name@gmail.com").status_code == 200          # an email works too
    again = _client(m)
    assert again.post("/auth/login", json={"username": "Mansi_K", "password": "correct horse"}).status_code == 200
    assert again.get("/auth/me").json()["user"]["name"] == "Mansi"
    assert again.post("/auth/login", json={"username": "mansi_k", "password": "nope"}).json()["detail"] == "Wrong username or password."


def test_a_domain_rule_rejects_plain_usernames(env):
    m, _ = env                                                                        # fixture sets acme.com
    r = _register(_client(m), "Ann", "ann_lee")
    assert r.status_code == 400 and "company email" in r.json()["detail"]
