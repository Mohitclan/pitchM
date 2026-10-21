"""The built-in practice meeting: demo kit, isolation from real data, exclusion from stats."""
import asyncio
from pathlib import Path

from backend import demo_kit


def test_every_step_has_a_voice_line_and_an_expectation():
    audio = Path("frontend/demo/audio")
    kinds = {"script", "mood", "answer_company", "answer_clinic", "objection", "not_in_notes", "lost", "talk",
             "answer_data", "commit", "finish"}
    ids = [s["id"] for s in demo_kit.STEPS]
    assert len(ids) == len(set(ids)) == 11
    for s in demo_kit.STEPS:
        assert (audio / f"{s['id']}.m4a").stat().st_size > 5000, s["id"]
        assert s["expect"] in kinds and s["client"] and s["you"] and s["watch"]
    assert {s["expect"] for s in demo_kit.STEPS} == kinds


def test_demo_documents_answer_the_demo_questions():
    lib = demo_kit.hub_library()
    by_id = {s["id"]: s["client"] for s in demo_kit.STEPS}
    assert "Pricing" in lib.search(by_id["c3"])[0]["title"]
    assert "Clinic Suite" in lib.search(by_id["c4"])[0]["title"]
    assert lib.search(by_id["c6"]) == []                         # SOC 2: must not be answerable
    assert "Pricing" in lib.search(by_id["c9"])[0]["title"]


def test_practice_session_uses_only_its_own_documents_and_script(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    from backend.session_state import SessionState
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(m, "_company_cache", {"version": None, "all": None, "by_venture": {}, "names": []})
    store = m.get_userdata()
    store.add_doc(m.COMPANY_ID, "Real pricing", "Real product costs 5 dollars per clinic per month for the whole platform.", "Real Co")
    store.add_doc("u1", "My notes", "My own notes say the platform costs 7 dollars per clinic per month.")
    st = SessionState(session_id="s", user_id="u1", practice_meeting=True)
    st.company_library = demo_kit.hub_library()
    m._load_script(st, {"script_id": "whatever", "use_library": True, "venture": "Real Co"})
    assert st.script_follower and st.library is None and st.script_id == "practice"
    hits, _ = m._knowledge_hits(st, "how much does the platform cost per clinic")
    assert hits and all("Real" not in h["title"] and "My notes" not in h["title"] for h in hits)
    hits, _ = m._knowledge_hits(st, "How much does Billing Hub cost per clinic, and is there a setup fee?")
    assert hits[0]["title"].startswith("Company: Billing Hub")


def test_practice_sessions_stay_out_of_stats_and_memory(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    from fastapi.testclient import TestClient
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "x.db"))
    store = db.FirestoreClient()
    monkeypatch.setattr(m, "_db", store)

    async def seed():
        await store.save_session("real1", {"session_id": "real1", "user_id": "alice", "created_at": 100, "session_kind": "meeting",
                                           "client": "Acme", "overall_score": 80, "duration_seconds": 300})
        await store.save_session("prac1", {"session_id": "prac1", "user_id": "alice", "created_at": 200, "session_kind": "meeting",
                                           "client": "Northwind Dental", "overall_score": 60, "duration_seconds": 600,
                                           "practice_meeting": True})
    asyncio.run(seed())
    c = TestClient(m.app)
    h = {"x-user-id": "alice"}
    assert [x["name"] for x in c.get("/api/clients", headers=h).json()["clients"]] == ["Acme"]
    assert [p["created_at"] for p in c.get("/api/sessions/trends", headers=h).json()["points"]] == [100]
    listed = c.get("/api/sessions", headers=h).json()["sessions"]
    assert {s["session_id"]: s["practice_meeting"] for s in listed} == {"real1": False, "prac1": True}
    assert asyncio.run(m._previous_call_for("alice", "Northwind Dental")) == {}
    kit = c.get("/api/demo-kit", headers=h).json()
    assert kit["completed"] is True and len(kit["steps"]) == 11
    assert c.get("/api/demo-kit", headers={"x-user-id": "bob"}).json()["completed"] is False
