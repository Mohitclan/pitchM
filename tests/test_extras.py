"""Tests for the meeting/practice extras: talk time, commitments, past-call memory,
rehearsal, rewrites, trends, goals and recap export."""
import asyncio


def _drain(state):
    return [state.ws_event_queue.get_nowait() for _ in range(state.ws_event_queue.qsize())]


def test_commitment_detection_and_parsing():
    from backend import coach_extras as cx
    assert cx.looks_like_commitment("Sure, I'll send you the pricing deck by Friday.")
    assert cx.looks_like_commitment("Let me check with legal and get back to you")
    assert not cx.looks_like_commitment("Let me explain how it works")
    assert not cx.looks_like_commitment("I will")                      # too short
    items = cx.parse_commitments("COMMIT: Send the pricing deck | Friday\nCOMMIT: Book a demo | -\nnoise")
    assert items == [{"task": "Send the pricing deck", "due": "Friday"}, {"task": "Book a demo", "due": ""}]
    assert cx.parse_commitments("NONE") == []
    assert cx.fallback_commitment("Thanks. I'll send the deck tonight. Bye.")["task"] == "I'll send the deck tonight."
    existing = [{"task": "Send the pricing deck"}]
    assert cx.is_duplicate_commitment("send pricing deck", existing)
    assert not cx.is_duplicate_commitment("Schedule the pilot kickoff", existing)


def test_commitments_are_tracked_live(monkeypatch):
    import backend.main as m
    from backend.session_state import SessionState
    s = SessionState(session_id="t-commit", session_kind="meeting")

    async def fake_quick(prompt, **kw):
        return "COMMIT: Send the pricing deck | Friday"

    monkeypatch.setattr(m, "_quick_text", fake_quick)
    asyncio.run(m._extract_commitments(s, "user", "Great, I'll send the pricing deck by Friday."))
    asyncio.run(m._extract_commitments(s, "user", "Yes I will send you the pricing deck."))   # duplicate
    assert [(c["owner"], c["task"], c["due"]) for c in s.commitments] == [("you", "Send the pricing deck", "Friday")]
    msgs = [x for x in _drain(s) if x.get("type") == "commitments"]
    assert len(msgs) == 1 and msgs[0]["added"][0]["task"] == "Send the pricing deck"

    async def no_model(prompt, **kw):
        return ""

    monkeypatch.setattr(m, "_quick_text", no_model)                    # no text model: keep the sentence
    asyncio.run(m._extract_commitments(s, "other", "We will get you the security questionnaire next week."))
    assert s.commitments[-1]["owner"] == "them" and "questionnaire" in s.commitments[-1]["task"]


def test_talk_time_monologue_and_share():
    import backend.main as m
    from backend.session_state import SessionState
    from backend.agents.tools import make_coaching_tools
    s = SessionState(session_id="t-talk", session_kind="meeting", coach_mode="sales_demo")
    make_coaching_tools(s)
    m._record_talk_time(s, {"kind": "monologue", "seconds": 40})       # under the limit: ignored
    assert not s.events
    m._record_talk_time(s, {"kind": "monologue", "seconds": 95})
    assert s.events[-1].event_type == "monologue" and s.events[-1].source == "sensor"
    assert any(x.get("type") == "coach_cue" for x in _drain(s))
    m._record_talk_time(s, {"kind": "talk_share", "share": 0.9})       # other side not heard yet: ignored
    assert len(s.events) == 1
    m._record_talk_time(s, {"kind": "stats", "you_s": 300, "them_s": 100, "longest_monologue_s": 95})
    assert s.talk_stats["you_share"] == 0.75 and s.talk_stats["longest_monologue_s"] == 95

    p = SessionState(session_id="t-pres", session_kind="meeting", coach_mode="presentation")
    make_coaching_tools(p)
    m._record_talk_time(p, {"kind": "monologue", "seconds": 400})     # presentations: one long turn is fine
    assert not p.events


def test_previous_call_summary_and_context():
    from backend import coach_extras as cx
    payload = {
        "session_id": "s1", "created_at": 1_700_000_000, "client": "Acme Retail", "session_kind": "meeting",
        "recap": {"summary": "Discussed pricing.", "open_questions": ["Volume discount?"],
                  "client_signals": ["Worried about setup time"],
                  "action_items": [{"owner": "You", "task": "Send case study", "due": ""}]},
        "commitments": [{"owner": "you", "task": "Send pricing deck"}, {"owner": "them", "task": "Share store list"}],
    }
    prev = cx.previous_call_summary(payload)
    assert prev["promised"] == ["Send pricing deck", "Send case study"]
    assert prev["their_commitments"] == ["Share store list"] and prev["objections"] == ["Worried about setup time"]
    ctx = cx.previous_call_context(prev)
    assert "Last call with Acme Retail" in ctx and "You promised: Send pricing deck" in ctx
    assert cx.client_key("  acme   RETAIL ") == "acme retail"


def test_rehearsal_and_rewrite_parsing():
    from backend import coach_extras as cx
    qs = cx.parse_rehearsal_questions(
        "Q: Why should we pay more than our current tool? | price vs status quo\n"
        "Q2: How long does setup take for 40 stores? | effort\nfiller line\n3. What if it fails? | risk")
    assert [q["question"] for q in qs] == [
        "Why should we pay more than our current tool?", "How long does setup take for 40 stores?", "What if it fails?"]
    assert qs[0]["why"] == "price vs status quo"
    assert len(cx.fallback_questions("unknown")) == 3
    rw = cx.parse_rewrites('```json\n[{"ts": 12, "original": "so um we kind of help", "rewrite": "We help stores.", "why": "direct"}, {"x": 1}]\n```')
    assert rw == [{"original": "so um we kind of help", "rewrite": "We help stores.", "why": "direct", "ts": 12.0}]
    assert cx.parse_rewrites("nope") == []


def test_trend_point_and_goals():
    from backend import coach_extras as cx
    payload = {
        "session_id": "s", "created_at": 1, "session_kind": "practice", "duration_seconds": 120, "overall_score": 72,
        "categories": {"filler_words": {"count": 6}, "eye_contact": {"drops": 2}},
        "transcript": [{"s": "u", "t": " ".join(["word"] * 300)}, {"s": "c", "t": "coach line"}],
    }
    pt = cx.session_trend_point(payload)
    assert pt["fillers_per_min"] == 3.0 and pt["wpm"] == 150 and pt["eye_drops_per_min"] == 1.0 and pt["score"] == 72
    meeting = dict(payload, session_kind="meeting", talk_stats={"you_s": 60})
    assert cx.session_trend_point(meeting)["wpm"] == 300                # pace over your own talking time
    assert cx.session_trend_point(dict(payload, insufficient_speech=True))["score"] is None
    goals = cx.clean_goals([{"metric": "fillers_per_min", "target": "3"}, {"metric": "fillers_per_min", "target": 2},
                            {"metric": "bogus", "target": 1}, {"metric": "score", "target": -5}, "x"])
    assert [(g["metric"], g["target"]) for g in goals] == [("fillers_per_min", 3.0)]


def test_scorecard_carries_extras():
    import time
    from backend.session_state import SessionState
    from backend.scorecard import build_scorecard
    s = SessionState(session_id="t-sc", session_kind="meeting")
    s.session_start = time.time() - 120
    s.add_transcript("user", " ".join(["hello"] * 40))
    s.client_name = "Acme"
    s.commitments = [{"ts": 3, "owner": "you", "task": "Send deck", "due": ""}]
    s.talk_stats = {"you_s": 60, "them_s": 40, "you_share": 0.6}
    sc = build_scorecard(s)
    assert sc["client"] == "Acme" and sc["commitments"][0]["task"] == "Send deck" and sc["talk_stats"]["you_share"] == 0.6
    s2 = SessionState(session_id="t-sc2")
    s2.session_start = time.time() - 30
    assert build_scorecard(s2)["insufficient_speech"] and build_scorecard(s2)["commitments"] == []


def test_brief_sets_client_and_loads_previous_call(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db
    from backend.session_state import SessionState
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "pc.db"))
    store = db.FirestoreClient()
    monkeypatch.setattr(m, "_db", store)

    async def run():
        await store.save_session("old-1", {"user_id": "u1", "created_at": 10, "session_kind": "meeting", "client": "Acme Retail",
                                           "recap": {"summary": "Pricing call.", "open_questions": ["Discount?"]}})
        await store.save_session("old-2", {"user_id": "u1", "created_at": 20, "session_kind": "meeting", "client": "Other Co"})
        s = SessionState(session_id="new", user_id="u1", session_kind="meeting")
        m._apply_brief(s, {"client": "acme retail", "notes": "", "points": ["Agree pilot"]})
        await asyncio.sleep(0.05)
        return s

    s = asyncio.run(run())
    assert s.client_name == "acme retail" and s.previous_call["session_id"] == "old-1"
    assert "Still open from last time: Discount?" in m._brief_block(s)
    assert any(x.get("type") == "previous_call" for x in _drain(s))


def test_extras_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as m
    from backend import db
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "api.db"))
    store = db.FirestoreClient()
    monkeypatch.setattr(m, "_db", store)
    asyncio.run(store.save_session("m-1", {
        "user_id": "tester-a", "created_at": 50, "session_kind": "meeting", "client": "Acme", "duration_seconds": 300,
        "overall_score": 70, "categories": {"filler_words": {"count": 5}}, "transcript": [{"s": "u", "t": "hi " * 100}],
        "recap": {"summary": "Good call.", "action_items": [{"owner": "You", "task": "Send deck", "due": "Fri"}]},
    }))
    client = TestClient(m.app)
    h = {"x-user-id": "tester-a"}

    assert client.get("/api/clients", headers=h).json()["clients"][0]["name"] == "Acme"
    assert client.get("/api/clients/last?name=ACME", headers=h).json()["call"]["promised"] == ["Send deck"]
    assert client.get("/api/clients/last?name=Acme", headers={"x-user-id": "someone-else"}).json()["call"] == {}

    pts = client.get("/api/sessions/trends", headers=h).json()["points"]
    assert pts[0]["fillers_per_min"] == 1.0

    assert client.get("/api/goals", headers=h).json()["goals"] == []
    put = client.put("/api/goals", json={"goals": [{"metric": "score", "target": 75}]}, headers=h)
    assert put.status_code == 200
    got = client.get("/api/goals", headers=h).json()
    assert got["goals"][0]["target"] == 75 and got["metrics"]["score"]["direction"] == "min"

    async def fake_quick(prompt, **kw):
        return "Q: Why is this worth the price? | cost\nQ: How fast is setup really? | effort\nQ: Who else uses it? | proof"

    monkeypatch.setattr(m, "_quick_text", fake_quick)
    qs = client.post("/api/rehearsal/questions", json={"mode": "sales_demo", "client": "Acme"}, headers=h).json()
    assert qs["source"] == "ai" and len(qs["questions"]) == 3

    async def fake_review(prompt, **kw):
        return '[{"index": 0, "rating": 3, "verdict": "Partly", "improve": "Lead with ROI", "better_answer": "It pays back in 3 months."}]'

    monkeypatch.setattr(m, "_quick_text", fake_review)
    rv = client.post("/api/rehearsal/review", json={"mode": "sales_demo", "items": [
        {"question": "Why is this worth it?", "answer": "Because um it is good"}, {"question": "Unanswered?", "answer": ""}]},
        headers=h).json()["review"]
    assert len(rv) == 1 and rv[0]["rating"] == 3 and rv[0]["better_answer"].startswith("It pays back")

    assert client.post("/api/sessions/m-1/export", headers=h).status_code == 404   # no webhook configured
    assert client.get("/api/config").json()["export_webhook"] is False


def test_stumbles_count_repeats_not_normal_doubles():
    from backend.session_state import count_stumbles
    assert count_stumbles("It it is integrated. on on HRMS. Chief OS Chief OS is our system.") == 3
    assert count_stumbles("where you can see where you can see all the payments") == 1
    assert count_stumbles("it it it works") == 2                        # a triple is two extra copies
    assert count_stumbles("very very good, bye bye, no no") == 0          # normal doubles
    assert count_stumbles("We grew from 2 to 2 stores") == 0              # numbers aren't words here
    assert count_stumbles("That, that is the point") == 1                 # punctuation doesn't hide it


def test_setup_chatter_trimmed_at_edges_only():
    from backend.session_state import SessionState
    s = SessionState(session_id="t-chatter", session_kind="meeting")
    s.transcript = [
        {"s": "u", "t": "start bolna start listen", "ts": 3.2, "te": 3.2},
        {"s": "u", "t": "क्या बोलूं? पिच कर दे।Hello, so we are going to the billing hub today.", "ts": 14.1, "te": 14.1},
        {"s": "o", "t": "Okay, go ahead.", "ts": 20.0, "te": 20.0},
        {"s": "u", "t": "Billing Hub is a centralized platform for payments across thirty ventures.", "ts": 30.0, "te": 40.0},
        {"s": "u", "t": "खत्म नहीं हुए।", "ts": 60.0, "te": 60.0},                       # mid-talk: kept
        {"s": "u", "t": "It also has a dashboard for every payment and churn.", "ts": 80.0, "te": 95.0},
        {"s": "u", "t": "हो गया हो गया खत्म कर।", "ts": 100.0, "te": 100.0},
    ]
    removed = s.trim_setup_chatter()
    texts = [seg["t"] for seg in s.transcript if seg["s"] == "u"]
    assert texts[0] == "Hello, so we are going to the billing hub today."
    assert "खत्म नहीं हुए।" in texts and texts[-1].startswith("It also has a dashboard")
    assert [r["ts"] for r in removed] == [3.2, 14.1, 100.0]
    assert [seg["t"] for seg in s.transcript if seg["s"] == "o"] == ["Okay, go ahead."]   # the other side untouched

    plain = SessionState(session_id="t-clean")
    plain.transcript = [{"s": "u", "t": f"Real sentence number {i} about the product.", "ts": i * 10.0, "te": i * 10.0}
                        for i in range(5)]
    assert plain.trim_setup_chatter() == [] and len(plain.transcript) == 5


def test_facts_extracted_and_deduped(monkeypatch):
    import backend.main as m
    from backend import coach_extras as cx
    from backend.session_state import SessionState
    s = SessionState(session_id="t-facts", session_kind="meeting")
    s.add_transcript("other", "I'm Priya, head of ops. We have 40 stores and want to go live in March.")
    replies = iter([
        "FACT: PERSON | Priya, Head of Ops | Them\nFACT: NUMBER | 40 stores | Them\nFACT: DATE | Go live in March | Them",
        "FACT: NUMBER | 40 stores | Them\nFACT: TOOL | Uses spreadsheets today | Them",
    ])

    async def fake_quick(prompt, **kw):
        return next(replies)

    monkeypatch.setattr(m, "_quick_text", fake_quick)
    asyncio.run(m._run_facts_check(s))
    asyncio.run(m._run_facts_check(s))
    assert [(f["kind"], f["text"]) for f in s.facts] == [
        ("person", "Priya, Head of Ops"), ("number", "40 stores"), ("date", "Go live in March"),
        ("tool", "Uses spreadsheets today")]
    assert "Facts noted so far" in m._brief_block(s)
    assert any(x.get("type") == "facts" for x in _drain(s))
    assert cx.FACT_HINT_RE.search("we have 40 stores") and not cx.FACT_HINT_RE.search("sounds good to me")
    assert cx.parse_facts("NONE") == [] and cx.parse_facts("FACT: MOOD | happy | Them") == []


def test_pitch_versions_number_and_points(tmp_path, monkeypatch):
    import backend.main as m
    from backend import db, coach_extras as cx
    from backend.session_state import SessionState
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "pv.db"))
    store = db.FirestoreClient()
    monkeypatch.setattr(m, "_db", store)

    async def fake_quick(prompt, **kw):
        return "COVERED: 1, 3"

    monkeypatch.setattr(m, "_quick_text", fake_quick)

    async def run():
        await store.save_session("v1", {"user_id": "u1", "created_at": 10, "session_kind": "practice", "overall_score": 60,
                                        "duration_seconds": 120, "pitch": {"name": "Billing Hub pitch", "version": 1, "points": []}})
        s = SessionState(session_id="v2", user_id="u1")
        m._apply_pitch(s, {"name": "  billing hub PITCH ", "points": ["Payments for 30 ventures", "Stripe and Razorpay", "Churn dashboard"]})
        s.add_transcript("user", " ".join(["word"] * 30))
        await m._score_pitch_version(s)
        return s

    s = asyncio.run(run())
    assert s.pitch["version"] == 2 and s.pitch["name"] == "billing hub PITCH"
    assert [p["covered"] for p in s.pitch["points"]] == [True, False, True]
    groups = cx.group_pitch_versions([
        {"session_id": "a", "created_at": 1, "pitch": {"name": "Billing Hub", "version": 1, "points": []}, "overall_score": 50},
        {"session_id": "b", "created_at": 2, "pitch": {"name": "billing hub", "version": 2,
                                                       "points": [{"text": "x", "covered": True}]}, "overall_score": 70},
        {"session_id": "c", "created_at": 3},
    ])
    assert len(groups) == 1 and [v["version"] for v in groups[0]["versions"]] == [1, 2]
    assert groups[0]["versions"][1]["points_hit"] == 1


def test_roleplay_agent_and_kickoff():
    import backend.main as m
    from backend.agents.live_coach import make_live_coach_agent
    from backend.session_state import SessionState

    class FakeQueue:
        def __init__(self):
            self.sent = []

        def send_content(self, content):
            self.sent.append(content.parts[0].text)

    s = SessionState(session_id="t-rp", coach_mode="pitch", roleplay_role="investor", persona="vc")
    agent = make_live_coach_agent(s)
    assert agent.name == "roleplay_partner" and agent.tools == []
    assert "investor" in agent.instruction and "never give feedback" in agent.instruction
    assert callable(s.flag_issue_fn)                                    # measurements still recorded
    s.live_queue = FakeQueue()
    m._apply_pitch(s, {"name": "Billing Hub", "points": ["30 ventures"]})
    m._roleplay_kickoff(s)                                              # only once
    assert len(s.live_queue.sent) == 1 and "Billing Hub" in s.live_queue.sent[0]

    coach = SessionState(session_id="t-coach")
    assert make_live_coach_agent(coach).name == "live_coach"


def test_pitches_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as m
    from backend import db
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setattr(db, "LOCAL_DB_PATH", str(tmp_path / "pa.db"))
    store = db.FirestoreClient()
    monkeypatch.setattr(m, "_db", store)
    asyncio.run(store.save_session("p1", {"user_id": "tester-a", "created_at": 5, "session_kind": "practice",
                                          "pitch": {"name": "Chief OS", "version": 1, "points": []}, "roleplay_role": "client"}))
    client = TestClient(m.app)
    body = client.get("/api/pitches", headers={"x-user-id": "tester-a"}).json()
    assert body["pitches"][0]["name"] == "Chief OS"
    row = client.get("/api/sessions", headers={"x-user-id": "tester-a"}).json()["sessions"][0]
    assert row["pitch_name"] == "Chief OS" and row["pitch_version"] == 1 and row["roleplay_role"] == "client"
