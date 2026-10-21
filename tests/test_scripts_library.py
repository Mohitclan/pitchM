"""Prepare me, script follow-along, own documents and the readiness check."""
import pytest

from backend import library, prepare, readiness, script_follow, userdata

SCRIPT = """## Opening
Thanks for making the time today.
Today I will show how Billing Hub centralises your clinic payments.

## What we do
Billing Hub collects every patient payment in one dashboard.
It reconciles insurance claims automatically each night.

## Next step
Can we book a pilot call next Wednesday?
"""

DOCS = [
    {"doc_id": "d1", "title": "Pricing sheet", "text": "Billing Hub costs 49 dollars per clinic per month. Annual plans get two months free."},
    {"doc_id": "d2", "title": "FAQ", "text": "Can it integrate with Zoho? Yes, the Zoho integration is included. If you stop, you can export all your data as CSV."},
    {"doc_id": "d3", "title": "Security", "text": "All data is encrypted at rest. Servers are hosted in the EU."},
]


def test_script_parse_and_format_roundtrip():
    sections = script_follow.parse_script_text(SCRIPT)
    assert [s["title"] for s in sections] == ["Opening", "What we do", "Next step"]
    assert sum(len(s["lines"]) for s in sections) == 5
    assert script_follow.parse_script_text(script_follow.format_script(sections)) == sections


def test_follower_tracks_paraphrase_and_flags_skipped_section():
    f = script_follow.ScriptFollower(script_follow.parse_script_text(SCRIPT))
    assert f.feed("so thanks for making the time today")
    assert f.feed("I want to show how Billing Hub centralises clinic payments")
    assert f.pos == 1
    assert not f.feed("anyway the weather has been lovely this week")        # chatter matches nothing
    assert f.feed("can we book a pilot call next Wednesday")                  # jumped to the end
    assert f.skipped_sections() == [1] and f.status()["pct"] < 100


def test_follower_locate_and_goto():
    f = script_follow.ScriptFollower(script_follow.parse_script_text(SCRIPT))
    idx, conf = f.locate("it reconciles the insurance claims every night")
    assert idx == 3 and conf >= 0.45
    f.goto(3)
    assert f.where_you_are()["next"][0].startswith("It reconciles")


def test_library_search_finds_the_right_document_and_ignores_unrelated():
    lib = library.Library(DOCS)
    assert lib.search("what does it cost per clinic")[0]["title"] == "Pricing sheet"
    assert lib.search("do you integrate with Zoho")[0]["title"] == "FAQ"
    assert lib.search("what happens to our data if we stop")[0]["title"] == "FAQ"
    assert lib.search("tell me about your football team") == []
    assert "Pricing sheet" in library.facts_block(lib.search("cost per clinic"))


def test_extract_text_rejects_unknown_types():
    assert "hello world document" in library.extract_text("a.txt", b"hello world document")
    with pytest.raises(ValueError):
        library.extract_text("a.exe", b"MZ")


def test_parse_prepared_handles_fences_and_garbage():
    raw = '```json\n{"name":"X","sections":[{"title":"Opening","lines":["Hi there."]}],"key_points":["a"],' \
          '"objections":[{"label":"Price","answer":"Pays back fast."}],"missing":["case study"]}\n```'
    p = prepare.parse_prepared(raw)
    assert p["name"] == "X" and p["text"].startswith("## Opening") and p["missing"] == ["case study"]
    assert prepare.objections_text(p["objections"]) == "Price: Pays back fast."
    assert prepare.parse_prepared("not json") is None
    assert prepare.parse_prepared('{"sections": []}') is None


def test_store_is_per_user(tmp_path):
    st = userdata.UserDataStore(str(tmp_path / "u.db"))
    s = st.save_script("alice", {"name": "Call", "text": SCRIPT})
    assert st.get_script("bob", s["script_id"]) is None
    assert st.list_scripts("bob") == [] and not st.delete_script("bob", s["script_id"])
    with pytest.raises(KeyError):
        st.save_script("bob", {"name": "Hijack", "text": SCRIPT}, script_id=s["script_id"])
    d = st.add_doc("alice", "Pricing", "Billing Hub costs 49 dollars per clinic per month.")
    assert st.get_docs("bob") == [] and not st.delete_doc("bob", d["doc_id"])
    with pytest.raises(ValueError):
        st.save_script("alice", {"name": "", "text": SCRIPT})
    with pytest.raises(ValueError):
        st.add_doc("alice", "Empty", "x")


def _ready(**kw):
    base = dict(sections=[{"title": "Opening", "covered": 2, "total": 2}, {"title": "Close", "covered": 2, "total": 2}],
                skipped=[], coverage_pct=100, key_points_hit=3, key_points_total=3, duration_s=300,
                target_minutes=5, clutter_per_min=1.0, wpm=140)
    base.update(kw)
    return readiness.readiness_check(**base)


def test_readiness_verdicts():
    assert _ready()["verdict"] == "ready"
    almost = _ready(clutter_per_min=9.0, wpm=210)
    assert almost["verdict"] in ("almost", "not_yet") and len(almost["fix"]) == 2
    bad = _ready(sections=[{"title": "Opening", "covered": 0, "total": 2}], skipped=["Opening"], coverage_pct=30)
    assert bad["verdict"] == "not_yet" and "skipped" in " ".join(bad["fix"])


def test_ground_in_library_marks_unknowns():
    import backend.main as m
    from backend.session_state import SessionState
    st = SessionState(session_id="s", user_id="u")
    assert m._ground_in_library(st, "price?") == ""
    st.library = library.Library(DOCS)
    assert "Pricing sheet" in m._ground_in_library(st, "what does it cost per clinic")
    assert st.hint_sources["what does it cost per clinic"]["sources"] == ["Pricing sheet"]
    assert "nothing about this" in m._ground_in_library(st, "tell me about your football team")
    assert st.hint_sources["tell me about your football team"] == {"not_in_notes": True}


def test_endpoints_isolate_users(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    from fastapi.testclient import TestClient
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "e.db"))
    c = TestClient(m.app)
    a, b = {"x-user-id": "alice"}, {"x-user-id": "bob"}
    r = c.post("/api/scripts", json={"name": "Call", "text": SCRIPT}, headers=a)
    assert r.status_code == 200
    sid = r.json()["script_id"]
    assert c.get(f"/api/scripts/{sid}", headers=a).status_code == 200
    assert c.get(f"/api/scripts/{sid}", headers=b).status_code == 404
    assert c.get("/api/scripts", headers=b).json()["scripts"] == []
    assert c.post("/api/library", files={"file": ("pricing.txt", b"Billing Hub costs 49 dollars per clinic per month.")}, headers=a).status_code == 200
    assert c.post("/api/library", files={"file": ("x.exe", b"MZ")}, headers=a).status_code == 400
    assert c.get("/api/library", headers=b).json()["docs"] == []
    assert c.post("/api/prepare", json={"material": "short"}, headers=a).status_code == 400
