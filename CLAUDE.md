# Pitchlight (formerly PitchMirror) — project knowledge and current state

**Name:** the product is called **Pitchlight** (renamed 2026-10-21; pitchlight.ai, getpitchlight.com and
pitchlightai.com were unregistered at the time, no trademark search done yet). Only user-visible text was
renamed. Internal identifiers deliberately keep `pitchmirror` so saved data survives: localStorage keys
(`pitchmirror_*`), `PITCHMIRROR_*` env vars, `data/pitchmirror.db`, the Docker service/volume names, ADK
`APP_NAME`, `/health` service field, and the repo/folder names.

Read this first in a new session. Last updated 2026-10-21. `PROGRESS.md` is the detailed change log (one section per change); this file is the summary.

## What it is
PitchMirror is an **AI speaking coach**. It listens while you talk, gives short measured nudges, and writes a full review afterwards.

- **Audience:** people who pitch, interview, sell or present, especially on video calls. Currently a pilot for the owner's colleagues at panscience.ai.
- **Origin:** a Gemini Live Agent Challenge project (`SmartGridsML/GeminiLiveAgentHack`, upstream commit `4448f05`), heavily extended here.
- **Owner:** Mansi (panscience.ai), with GitHub account **Mohitclan** ("Mohit kumar").

### Two entry points
1. **Practice app (`/app`)**
   - Intake: coach persona (Professional / Brutal VC / Supportive Mentor), prep type, delivery context, goal.
   - Live transcript on the left, Coach Suggestion cards on the right.
   - Measured flags for fillers, pace (WPM, 20 s window) and eye contact (MediaPipe gaze in the browser).
   - Spoken cues. Optional slide PDF.
   - End: scorecard with a side-by-side transcript and coach review, plus .txt download.
2. **Meeting Mode (`/meeting`)**
   - A dark **notch** (pill that drops down) in an always-on-top Document Picture-in-Picture window; on-page fallback.
   - Silent text nudges only. No coach audio in meetings.
   - Pre-call brief (notes, points to cover, planned length) with a progress ring.
   - **Choose meeting audio** (tab audio) so the other person is transcribed too.
   - Help buttons: Answer, Catch up, Ask.
   - Live answer outline when the client asks a question, a client-mood chip, answer timer, You/Them meters, 👍/👎 on tips, and a smart-quiet mode.
   - After the call: recap (decisions, action items, open questions, signals), a ready-to-send follow-up email (Copy / Open in mail app), a question-by-question review with stronger answers, and the transcript.

3. **Added 2026-10-21** (see PROGRESS.md "Meeting + practice extras"): talk-time / monologue nudges, live
   commitments tracker, past-call memory per client, pre-call rehearsal (`/ws?kind=rehearsal`), recap export
   (Markdown / webhook), progress charts + practice goals in History, clickable replay markers, side-by-side
   "stronger version" with read-aloud and retry. Helpers in `backend/coach_extras.py`, shared mic/voice code in
   `frontend/rehearse.js`.
   Later the same day: live **facts card** (meeting), **pitch versions** with comparison, local **video replay**
   (IndexedDB, never uploaded) and an **AI role-play partner** (`?roleplay=investor|interviewer|client`) in practice.
   Then **live advice**: meeting "How chatty?" (`?chatty=quiet|balanced|active`) with next-move / missed-question /
   positive notes, objection cards from prepared answers, a question coach and a next-step guard; practice side
   panel with live stats, key-point checklist, advice cards and "Say it better" (`?feedback=live|end`).

**Accounts (2026-10-22):** `AUTH_MODE=accounts` makes every person sign in (`/login`) and keeps their data private
(`backend/auth.py`, guide in `docs/ORG_ROLLOUT.md`). Off by default; the old "Who's testing?" picker remains for a
one-laptop pilot.

Other pages: `/` landing, `/guide` ("How to try it"), `/pilot` feedback summary, History (header link). A tester picker ("Who's testing?") separates users in one browser.

## Architecture
- **Backend:** FastAPI + uvicorn (`backend/main.py`, large). Docker Compose service on port **8080**. SQLite at `/app/data/pitchmirror.db` in the named volume `pitchmirror_data` (survives rebuilds; `down -v` deletes it).
- **Live coach:** Google ADK `LlmAgent` + `Runner.run_live` with `gemini-2.5-flash-native-audio-preview-12-2025`. Tools in `backend/agents/tools.py` (`flag_issue`, `check_eye_contact`, `get_speech_metrics`, …). The server flags measured issues itself (`server_flag_issue`), with two-signal gates and cooldowns. Meeting mode uses 12 s global and 60 s per-type cooldowns.
- **Transcription:** a dedicated `LiveTranscriber` per audio stream (`backend/transcriber.py`, `gemini-3.5-transcribe-live`).
  - One for your mic, and one for the meeting audio, started lazily on the first 0x06 frame.
  - Segments arrive after about a 2 s pause. Sessions rotate before the ~10 min cap, replaying unconfirmed audio. A stall watchdog restarts it.
  - Replaced the browser Web Speech approach, which returned 0–4 words.
- **Text work uses Claude** (`backend/claude_client.py`, Anthropic SDK): Opus 5.5 for reports, recap and Q&A review (`report_text`); Haiku 5.5 for live answer outlines and conversation checks (`fast_text`). **Gemini is the automatic fallback**: `gemini-3.5-flash` → `gemini-2.5-flash` → `gemini-3.1-flash-lite`, 40 s per model. Gemini still does all live audio.
- **Post-session:** parallel Delivery, Content, Research, QA-review and Recap agents (`backend/agents/post_session.py`), then synthesis. `backend/scorecard.py` builds the result, with **measured floors** so the AI can't hide measured problems. Under 25 spoken words there is no score (`insufficient_speech`).
- **Browser audio:** `frontend/audio-leveler.js` (high-pass, AGC worklet, limiter) for both voices.
- **Key files**
  - `backend/session_state.py`: transcript merging, metrics, Q&A extraction, gaze streaks.
  - `backend/meeting_brief.py`: brief and coverage prompts.
  - `backend/db.py`: SQLite (sessions, user_profiles, feedback).
  - `frontend/meeting.js`, `bubble.css` (the notch), `meeting.css` (page only), `app.js`, `gaze.js`, `tester.js`.

### WebSocket protocol (`/ws?kind=practice|meeting&…`)
- **Binary frames, first byte:** `0x01` mic PCM, `0x02` video, `0x03` stop, `0x04` screen, `0x05` slide, `0x06` meeting (other) audio, `0x10` coach audio (server→client).
- **Client JSON:** `brief` (now with `client`), `help`, `nudge_feedback`, `interruption`, `gaze`, `talk_time`, `pitch` and `say_better` (practice).
- **Server JSON:** `status`, `transcript`, `transcript_end`, `coach_tip`, `coach_cue`, `coach_said`, `answer_hint`, `help_reply`, `client_mood`, `stt_status`, `brief_points`, `brief_reminder`, `commitments`, `previous_call`, `facts`, `advice`, `objection_card`, `questions`, `live_stats`, `pitch_points`, `say_better`, `pipeline_step`, `scorecard`.

## Configuration (`.env`, gitignored — never print or commit values)
- `GOOGLE_API_KEY`: a free key.
- `ANTHROPIC_API_KEY`: a user key (`sk-ant-usr-…`). **Requires** `ANTHROPIC_WORKSPACE_ID`, sent as the `anthropic-workspace-id` header.
- Also set: `PITCHMIRROR_ANALYSIS_MODEL`, `PITCHMIRROR_RESEARCH_MODEL`, `PITCHMIRROR_SUMMARY_MODEL`, `PITCHMIRROR_FALLBACK_MODELS`, `PITCHMIRROR_RESEARCH_SEARCH=false`, `ENABLE_IMAGE_GENERATION=false`, and raised `WS_*` limits.
- `.env.example` documents all variables. `--reload` / `up -d` need a recreate after `.env` changes.
- A key belongs to the account that created it, so signing into another Claude account doesn't change which credits are used.
- Cost: roughly $0.25–0.30 per meeting in Claude credits.

## How to run
- **"run server"** means: bring up the full Docker stack and verify landing, `/app` and `/meeting` (see memory).

  ```bash
  cd /Users/admin/Desktop/hub/GeminiLiveAgentHack && docker compose up -d --build
  ```

  Open Docker Desktop first with `open -a Docker` if needed. macOS has no `timeout` command.
- **Tests:** `.venv/bin/python -m pytest tests/ -q` (106 tests). Live-model smoke test: `scripts/smoke_test.py`.
- **Chrome** with headphones is needed for the full experience.

## Where things stand (2026-10-21)
- **Built and tested:** everything above. A scripted end-to-end test of practice and meeting mode passed (hints, coverage, nudges, mood, help, recap, QA, ratings). A real one-sided Teams test of the notch worked (185 words in 3.5 min, 6 relevant nudges, help, review).
- **Git:**
  - Branch `pitchmirror-pilot` tracks **`pitchm` = https://github.com/Mohitclan/pitchM** (`main`). Latest commit `e8f06ac`, authored as Mohit kumar `<192804849+Mohitclan@users.noreply.github.com>`.
  - Commits keep the Claude co-author line (the owner chose to keep it).
  - `origin` is the original upstream, which the owner cannot push to. Push only as Mohitclan (`gh auth` active account); anj2609 is signed in but inactive.
  - Uncommitted: the `video/` folder and a `.gitignore` update.
- **Rollout plan:** pilot **only on the owner's laptop**. After the organisation approves, deploy to their AWS instance under a subdomain. The `infra/` Terraform is upstream's Cloud Run setup and is not used for this.

### Product films (new, in `video/`)
- `out/product-overview-60s.mp4` (60 s) and `out/product-walkthrough-90s.mp4` (90 s). 1920×1080, 30 fps, H.264, stereo AAC at −16 LUFS.
- **Rules from the owner: everything free, and no Gemini or Claude calls while capturing.** The real pages were captured with a mocked WebSocket and demo data (fictional "Brightpath" / "Northwind Dental").
- Narration: Kokoro `af_heart`, run locally in Docker (`pitchmirror-kokoro` image, about 3.5 GB). Music is synthesised in `tts/music.py`. No Remotion, because it needs a paid licence for companies over 3 people; the engine is Playwright + FFmpeg.
- `video/README.md` explains how to re-capture, re-render and edit. `RESEARCH.md`, `storyboard.md`, the two scripts and `ASSET_MANIFEST.md` are alongside.

## Known limits and open items
- A Picture-in-Picture window can't dock to the top centre or resize itself. Chrome allows resizing only inside a click.
- Native Zoom/Teams desktop audio can't be captured from Chrome; use the browser tab (Windows can share system audio).
- Headphones are required for interruption detection and smart quiet.
- **`/api/feedback/summary` has no authentication.** It is fine for the local pilot, but must be secured before any deployment. Set `API_BEARER_TOKEN` too.
- Not yet tested: a real multi-person call with a real client voice, and sessions over about 15 min with transcriber rollover in a live call.
- **Bug found:** `frontend/app.js` `handleServerMessage` has `case 'coach_cue'` twice, so the second one (the spoken-cue branch for practice mode, `speakCue`) never runs. Small fix: merge the two cases.
- Upstream pins `google-adk==1.27.0`, which is yanked on PyPI (still works).
- Free Gemini tier quotas are low: that is why Claude does the text work.

## Working rules and conventions
- Never print, log or commit API keys. `.env`, `data/`, `*.db` and `LOCAL_CHANGES.patch` are gitignored.
- Bust the browser cache with the `?v=` query on static files. HTML is served with `Cache-Control: no-cache`.
- Don't modify the production app just to make a video or demo.
- Match the surrounding code style; the notch's CSS lives only in `bubble.css`.
