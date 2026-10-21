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
    assert "Do NOT state any number" in m._ground_in_library(st, "tell me about your football team")
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


def test_office_files_are_read():
    import io, zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("ppt/slides/slide2.xml", "<s><a:p><a:r><a:t>Security: encrypted at rest</a:t></a:r></a:p></s>")
        z.writestr("ppt/slides/slide1.xml", "<s><a:p><a:r><a:t>Billing Hub</a:t></a:r></a:p><a:p><a:r><a:t>Costs 49 dollars</a:t></a:r></a:p></s>")
    text = library.extract_text("deck.pptx", b.getvalue())
    assert text.index("Slide 1") < text.index("Slide 2") and "Costs 49 dollars" in text
    with pytest.raises(ValueError):
        library.extract_text("bad.pptx", b"not a zip")


def test_company_hub_is_admin_only_and_used_as_fallback(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    from backend.session_state import SessionState
    from fastapi.testclient import TestClient
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "c.db"))
    monkeypatch.setattr(m, "_company_cache", {"version": None, "all": None, "by_venture": {}})
    c = TestClient(m.app)
    a = {"x-user-id": "alice"}
    r = c.post("/api/company", json={"title": "Pricing 2026", "venture": "Billing Hub",
                                     "text": "Billing Hub costs 49 dollars per clinic per month. Annual plans get two months free."}, headers=a)
    assert r.status_code == 200 and r.json()["venture"] == "Billing Hub"
    listing = c.get("/api/company", headers={"x-user-id": "bob"}).json()
    assert listing["can_edit"]                                           # accounts off: everyone is the admin
    assert [(v["name"], len(v["docs"])) for v in listing["ventures"]] == [("Billing Hub", 1)]
    assert c.post("/api/company/ventures", json={"name": "Clinic Suite", "description": "Practice management"}, headers=a).status_code == 200
    assert c.post("/api/company/ventures", json={"name": "clinic suite"}, headers=a).status_code == 400     # duplicate
    assert c.post("/api/company/ventures", json={"name": "General"}, headers=a).status_code == 400
    # a member (accounts on) can read but not write
    monkeypatch.setattr(m, "AUTH_ACCOUNTS", True)
    monkeypatch.setattr(m.accounts, "user_from_request", lambda req: {"user_id": "u2", "role": "member", "email": "b@x.com"})
    member = TestClient(m.app)
    assert member.get("/api/company").json()["can_edit"] is False
    assert member.post("/api/company", json={"title": "x", "text": "y" * 40}).status_code == 403
    assert member.delete(f"/api/company/{listing['ventures'][0]['docs'][0]['doc_id']}").status_code == 403
    monkeypatch.setattr(m, "AUTH_ACCOUNTS", False)
    # live answers: own documents first, company hub as the fallback, "not in notes" if neither has it
    st = SessionState(session_id="s", user_id="alice")
    st.company_library = m._company_library()
    assert st.company_library
    prompt = m._ground_in_library(st, "what does it cost per clinic")
    assert "Billing Hub · Pricing 2026" in prompt
    assert st.hint_sources["what does it cost per clinic"]["sources"] == ["Company: Billing Hub · Pricing 2026"]
    st.library = library.Library([{"doc_id": "p", "title": "Client notes", "text": "Priya wants the price per clinic in rupees and a pilot."}])
    assert st.hint_sources.clear() or "Client notes" in m._ground_in_library(st, "price per clinic")      # own layer wins
    assert "Neither" in m._ground_in_library(st, "tell me about your football team")


def test_business_picked_for_a_call_is_searched_first(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    from backend.session_state import SessionState
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "v.db"))
    monkeypatch.setattr(m, "_company_cache", {"version": None, "all": None, "by_venture": {}})
    store = m.get_userdata()
    store.add_doc(m.COMPANY_ID, "Pricing", "Billing Hub costs 49 dollars per clinic per month for the platform.", "Billing Hub")
    store.add_doc(m.COMPANY_ID, "Pricing", "Clinic Suite costs 199 dollars per clinic per month for the platform.", "Clinic Suite")
    st = SessionState(session_id="s", user_id="alice")
    st.company_library = m._company_library()
    st.venture = "Clinic Suite"
    hits, _ = m._knowledge_hits(st, "what does the platform cost per clinic")
    assert hits[0]["title"] == "Company: Clinic Suite · Pricing"
    st.venture = ""
    assert {h["title"] for h in m._knowledge_hits(st, "what does the platform cost per clinic")[0]} >= {"Company: Billing Hub · Pricing"}
    names = [v["name"] for v in store.list_ventures()]
    assert names == ["Billing Hub", "Clinic Suite"] and store.delete_venture("Clinic Suite") == 1


def test_business_summary_is_searchable_and_named_business_wins(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    from backend.session_state import SessionState
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "n.db"))
    monkeypatch.setattr(m, "_company_cache", {"version": None, "all": None, "by_venture": {}, "names": []})
    store = m.get_userdata()
    store.add_venture("Clinic Suite", "Clinic Suite is practice management software for dental clinics: appointments, charting and reminders.")
    store.add_venture("Billing Hub", "Billing Hub centralises patient payments and insurance claims.")
    store.add_doc(m.COMPANY_ID, "Pricing", "The platform costs 199 dollars per clinic per month for the whole team.", "Clinic Suite")
    store.add_doc(m.COMPANY_ID, "Pricing", "The platform costs 49 dollars per clinic per month for the whole team.", "Billing Hub")
    st = SessionState(session_id="s", user_id="alice")
    st.company_library = m._company_library()
    hits, _ = m._knowledge_hits(st, "what does Clinic Suite do for dental clinics")
    assert hits and hits[0]["title"] == "Company: Clinic Suite · About"
    st.venture = "Billing Hub"                         # call is about Billing Hub, but the client names Clinic Suite
    hits, _ = m._knowledge_hits(st, "and what does the Clinic Suite platform cost per clinic")
    assert hits[0]["title"] == "Company: Clinic Suite · Pricing"
    store.update_venture("Billing Hub", "Billing Hub is now a payments and claims product.")
    assert m._company_library("Billing Hub").search("payments and claims product")


def test_weak_overlap_does_not_count_as_an_answer():
    notes = library.Library([{"doc_id": "n", "title": "Client notes", "text":
        "Priya Sharma, Head of Operations at Northwind Dental. Worried about price, setup time and Zoho Books. "
        "Her CFO approves spend over 20000 dollars. Goal: agree a pilot and a follow up call."}])
    assert notes.search("Do you have SOC 2 certification? Our compliance team will definitely ask about that.") == []
    assert notes.search("what is Priya worried about with setup and price")[0]["cov"] >= 0.3


def test_own_notes_win_only_when_they_answer_as_well_as_the_hub(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    from backend.session_state import SessionState
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "w.db"))
    monkeypatch.setattr(m, "_company_cache", {"version": None, "all": None, "by_venture": {}, "names": []})
    m.get_userdata().add_doc(m.COMPANY_ID, "FAQ", "If you stop using Billing Hub you can export all your data as CSV files for 30 days.", "Billing Hub")
    st = SessionState(session_id="s", user_id="u")
    st.company_library = m._company_library()
    st.library = library.Library([{"doc_id": "n", "title": "Notes", "text": "Priya is worried about setup time and the price per clinic."}])
    hits, _ = m._knowledge_hits(st, "what happens to our data if we stop using Billing Hub")
    assert hits[0]["title"].startswith("Company: ")
    hits, _ = m._knowledge_hits(st, "what is Priya worried about, setup time and price per clinic")
    assert hits[0]["title"] == "Notes"


def test_locate_prefers_the_latest_line_when_words_span_several():
    f = script_follow.ScriptFollower(script_follow.parse_script_text(SCRIPT))
    recent = ("thanks for making the time today Today I will show how Billing Hub centralises your clinic payments "
              "Billing Hub collects every patient payment in one dashboard It reconciles insurance claims automatically each night")
    assert f.locate(recent)[0] == 3


def test_business_name_matches_even_when_transcribed_without_a_space(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    from backend.session_state import SessionState
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(m, "_company_cache", {"version": None, "all": None, "by_venture": {}, "names": []})
    store = m.get_userdata()
    store.add_doc(m.COMPANY_ID, "Pricing", "The platform costs 199 dollars per clinic per month for the whole team.", "Clinic Suite")
    store.add_doc(m.COMPANY_ID, "Pricing", "The platform costs 49 dollars per clinic per month for the whole team.", "Billing Hub")
    st = SessionState(session_id="s", user_id="u")
    st.company_library = m._company_library()
    st.venture = "Billing Hub"
    hits, _ = m._knowledge_hits(st, "what does ClinicSuite cost per clinic for the platform")
    assert hits[0]["title"] == "Company: Clinic Suite · Pricing"
