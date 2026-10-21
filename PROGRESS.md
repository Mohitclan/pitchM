# PitchMirror — local progress notes (handoff for a new session)

Last updated: 2026-10-19. Upstream: https://github.com/SmartGridsML/GeminiLiveAgentHack (cloned at commit `4448f05`).
All work below is **uncommitted** in this folder. A backup of every code change is in `LOCAL_CHANGES.patch`
(`git apply LOCAL_CHANGES.patch` on a fresh clone of `4448f05` restores everything, including the new `frontend/gaze.js`).

## How to run

```bash
cd /Users/admin/Desktop/hub/GeminiLiveAgentHack
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8080 --reload
# open http://localhost:8080/app in Chrome (needs webcam + mic)
```

- Python venv: `.venv` (Python 3.12). Homebrew's ensurepip wheel is unreadable, so the venv was made with
  `python3.12 -m venv --without-pip .venv && python3.12 -m pip --python .venv/bin/python install pip`.
- Tests: `.venv/bin/python -m pytest tests/ -q` → 34 passing.
- Smoke test of the live model: `set -a && . ./.env && set +a && .venv/bin/python scripts/smoke_test.py`.
- Desktop-app preview config lives one level up: `/Users/admin/Desktop/hub/.claude/launch.json` (name `pitchmirror`).
- `--reload` does NOT pick up `.env` edits — restart the server after changing `.env`.

## .env (gitignored) — settings that matter

Free-tier Gemini key is in `GOOGLE_API_KEY` (don't commit or paste it anywhere). Non-default values:

```
GOOGLE_CLOUD_PROJECT=                      # empty → Firestore disabled; sessions not persisted
ENABLE_IMAGE_GENERATION=false              # Imagen not available on this key (404)
PITCHMIRROR_ANALYSIS_MODEL=gemini-3.8-flash   # gemini-2.5-flash is closed to new keys (404)
PITCHMIRROR_RESEARCH_MODEL=gemini-3-flash-preview
PITCHMIRROR_SUMMARY_MODEL=gemini-3.6-flash
PITCHMIRROR_RESEARCH_SEARCH=false          # Google Search grounding has 0 free-tier quota (429)
```

Live model `gemini-2.5-flash-native-audio-preview-12-2025` works (no daily cap on free tier).
Free-tier text models: 5 requests/min, 20 requests/day **per model** → each session uses ~3 calls on
3.8-flash + 1 on 3-flash-preview + 1 on 3.6-flash, so roughly 5–6 full sessions/day. Check usage at https://ai.dev/rate-limit.

## Problems found and what was changed

1. **Scorecard always 5/5.** Post-session agents 404'd (old model), and the fallback score only counted
   `flag_issue` interruptions, which are heavily gated (two-signal gate + cooldowns), so counts stayed 0.
   - `backend/session_state.py`: `transcript_filler_count()` / `scored_filler_count()` count unambiguous fillers
     (um, uh, erm, you know, basically, literally, kind of, sort of, I mean) straight from the presenter transcript.
     `scored_eye_drops()` = max of flagged drops, model gaze streaks, browser gaze streaks.
   - `backend/scorecard.py`: filler and eye scores = `min(AI score, measured score)` so the AI can't hide measured issues.
   - `backend/main.py`: METRICS line sent to report agents uses the measured counts.
2. **Quota (429) errors in the report.** `backend/coach.py` adds `RESEARCH_MODEL` / `SUMMARY_MODEL` env overrides;
   `backend/agents/post_session.py` uses them and drops `google_search` when `PITCHMIRROR_RESEARCH_SEARCH=false`;
   `backend/main.py` skips the "repair" retry when an agent fails with RESOURCE_EXHAUSTED.
3. **Eye contact never detected** (depended on the live model calling `check_eye_contact`).
   - New `frontend/gaze.js`: MediaPipe Face Landmarker (CDN, tasks-vision 0.10.14) at ~6 fps classifies
     `camera | away | notes | no_face` (head yaw/pitch from landmarks + eye blendshapes, pitch auto-calibrated in
     first frames, 5-frame majority smoothing). Sends `{"type":"gaze","state":...}` over the WebSocket on change + 1s heartbeat.
   - `frontend/app.js`: `startGazeTracking()` / `stopGazeTracking()`; the existing static "Camera OK" badge now shows
     live state (amber while away, red past threshold). `index.html` loads `gaze.js`; `style.css` badge colours.
   - `backend/session_state.py`: `update_browser_gaze()` times streaks (5s virtual / 6.5s hybrid / 8s in-person;
     sideways glances only count in virtual), counts each streak once, sets the `eye_contact_confirmed` signal.
   - `backend/main.py`: handles the `gaze` WS message; on a confirmed look-away logs `browser_gaze_confirmed` and
     nudges the live coach via `live_request_queue.send_content` to call `flag_issue("eye_contact")`.
   - `backend/agents/tools.py`: model-side `check_eye_contact` also counts confirmed streaks (`gaze_away_streaks`).
   - `tests/test_core.py`: 3 new tests for gaze streaks and the eye-score floor.

## Status / next steps

- Session 2 (before gaze tracking) scored 76/100 with a real AI report (pace 40, clarity 50, filler 80, eye 100).
- **Not yet verified with a real face:** browser gaze tracking. Test: start a session, look down 6+ s → badge turns
  red, coach should interrupt, scorecard eye drops ≥ 1. Server log line to look for: `browser_gaze_confirmed`.
  If "looking down" is too sensitive/insensitive, tune `pitchBaseline + 0.07`, `eyesDown > 0.55`,
  and yaw bounds `0.32/0.68` in `frontend/gaze.js`.
- Sessions aren't persisted (no Firestore) — results are only visible in the UI and server logs.
- Filler regex intentionally excludes "like", "so", "right" (too many false positives) → filler counts run slightly low.
- Upstream pins `google-adk==1.27.0`, which is yanked on PyPI (works locally).
- Test script used for sessions: a ~90 s pitch with sections for fillers, fast pace, looking away, a contradiction
  ("12 pilot stores" then "zero customers"), and an incoherent sentence.

## Fix: "Connection lost" / no transcript (after adding gaze tracking)

Cause: the first MediaPipe detection compiles GPU/WASM kernels and freezes the page for seconds; queued audio
chunks then flush in one burst and exceed the server's per-second WS message limit → server closes the socket
("WS traffic rate-limited" in logs). Fixes: `gaze.js` warms the model with one detection during preload and caps
gaze messages at 2/s; `.env` raised `WS_MAX_MESSAGES_PER_SECOND=120` and `WS_MAX_CONCURRENT_PER_IP=4`
(a page reload mid-session briefly leaves the old socket open → 403 "concurrent limit exceeded").

## Added: post-session transcript

- Scorecard now has a "Your Transcript" section (timestamped You/Coach lines, word count) and a
  "Download .txt" button — `renderTranscriptSection()` / `downloadTranscript()` in `frontend/app.js`, styles at
  the end of `frontend/style.css`. Data comes from `scorecard.transcript` (already sent by the server).
- Server transcript fix (`backend/session_state.py`): live transcription arrives as few-word fragments and the
  old cap kept only the last 80 → early parts of a pitch were lost (also from report AI + filler count).
  `add_transcript()` now merges same-speaker fragments (≤3 s gap, ≤8 s per segment; `te` = segment end) and the
  cap is 400 segments.

## Added (2026-10-19, later): Docker storage, live coach suggestions, side-by-side review, history

- **Storage:** `backend/db.py` saves sessions + user profiles to SQLite (`LOCAL_DB_PATH`, default `data/pitchmirror.db`)
  when Firestore is off. `docker compose up --build -d` mounts named volume `pitchmirror_data` at `/app/data`
  (survives rebuilds and `down`; only `down -v` deletes it).
- **Transcript fix:** ADK streams sub-word chunks (finished=False) and then repeats the whole utterance (finished=True).
  The old code stripped chunk spacing and stored both → "u ser frien dly" + duplicate lines + doubled word counts.
  Now chunks are concatenated raw (`add_transcript(..., raw=True)`), the finished copy is only an utterance boundary.
- **Look-away fix:** confirmed browser look-aways are flagged server-side (`_handle_confirmed_lookaway` in main.py →
  `state.flag_issue_fn`), then the model is asked only to say a fixed cue. Inside cooldown they're recorded quietly.
- **Leaked tool text:** coach speech matching `looks_like_tool_text` ("tool_code …") is suppressed; suppressed
  coach speech is no longer stored in the transcript. Report input says which events were actually voiced (`said`).
- **UI:** live board = transcript (left) + Coach Suggestions cards (right, newest first, `coach_tip`/`coach_cue`/
  `coach_said` WS messages). End view = transcript with ⚑ markers (left) + coach review & AI report (right),
  "Download .txt" (transcript + moments + report). Header "History" lists saved sessions; click opens the review.
- Sessions recorded before this fix still show fragmented words (data was stored that way).

## Added: Meeting Mode (floating coach bubble for Zoom / Meet / Teams)

- Landing page (`frontend/landing.html`) now has two entry points: **Practice in the app** (`/app`) and
  **Coach me in a real meeting** (`/meeting`), plus a Meeting Mode section and a header link in the app.
- `/meeting` → `frontend/meeting.html|js|css`. Start opens the coach bubble as a Document Picture-in-Picture window
  (Chrome/Edge 116+ desktop: always on top of every app, user-draggable); fallback is a draggable bubble on the page.
  Mic always; camera optional (gaze). Capture, gaze ticks and video frames are driven by the audio worklet so
  Chrome's background-timer throttling doesn't stall them.
- Backend: `/ws?kind=meeting&platform=zoom|google_meet|teams|webex|other`. Meeting sessions: never send coach audio,
  extra "LIVE MEETING MODE" prompt block, no screen/slides/demo, 90-min limit (`MEETING_MAX_MINUTES`), and
  `_meeting_nudge_watcher` flags fillers (≥3/30s) and sustained pace (>180 wpm) from transcript metrics every 3 s
  through `state.flag_issue_fn` (measured → skips the two-signal gate, keeps cooldowns).
- Saved with `session_kind`/`platform`; History shows Practice/Meeting badges. `/app?session=<id>` and
  `/app?view=history` deep links are used by the meeting page.
- Not yet verified with a real mic in a real call (in-app browser blocks the mic). Long (>15 min) Live API sessions
  not yet tested — watch for disconnects around the Live API connection limit.

## Fixed: meeting-mode transcription + real-time conversation coaching

Problem seen in a real test: 44 s of speech → 2 words ("ഹലോ", "भाई"); no nudges; report then failed on 503s
and still scored 100. Causes: the native-audio Live model's input transcription is a by-product (auto language
detection, bursty, `language_codes` unsupported on the Gemini API), the other participant was never captured,
and the scorecard scored near-empty transcripts.
- Presenter speech now uses the browser's speech recognition (Chrome/Edge Web Speech, language picker,
  `stt=browser`); final phrases go to the server as `{"type":"speech"}`. The mic PCM is still sent for voice
  measurements only (not to the model). Web Speech drops "um/uh", so filler counts run low.
- Other participant: "Choose meeting audio" → getDisplayMedia tab/system audio → frame type 0x06 → the live model,
  whose transcription becomes speaker "o" ("Them"). Mac native Zoom/Teams apps can't share audio from Chrome
  (only browser tabs); Windows can share system audio.
- `_meeting_nudge_watcher`: fillers / sustained pace / flat tone every 3 s, plus a `[coach check]` every ~25 s of
  new speech; the model replies `NONE` or `CATEGORY: nudge` (off_topic, rambling, unclear, monotone, hedging,
  contradiction) → parsed from its transcription → flagged. Meeting cooldowns 12 s global / 60 s per type;
  a blocked content nudge is retried once after the gap.
- Under 25 presenter words: no AI report, no score (`insufficient_speech`).
- Verified with scripted sessions against the real model (pace, flat tone, off-topic nudges; synthesized
  interviewer voice transcribed as "Them"). Not yet verified in a real call with a real voice.

## Fixed: voice not captured in Meeting Mode → dedicated live transcription model

Real tests: browser speech recognition produced 0–4 words in ~60 s although the mic audio reached the server fine
(12–13 s voiced per 20 s). Replaced it with `backend/transcriber.py`: one Gemini Live session per audio stream on
`gemini-3.5-transcribe-live` (TEXT modality + input transcription; `PITCHMIRROR_TRANSCRIBE_MODEL`, empty disables).
It emits one accurate segment ~1–2 s after each pause (handles Hinglish in Latin script); auto-reconnects.
- Mic (0x01) → user transcriber in both practice and meeting mode; the coach model's own input transcription is
  only used if the transcriber fails. In meeting mode the coach model gets no audio (works from text checks).
- Meeting audio (0x06) → "other" transcriber ("Them").
- Language picker and Web Speech code removed from the meeting page.
- Verified with synthesized voices: meeting both sides, practice mic. Not yet verified with a real human voice.

## Fixed: post-session report took ~5 min

`gemini-3.8-flash` (old PITCHMIRROR_ANALYSIS_MODEL) was overloaded: 504 after 60 s / 503 "high demand"; each agent
waited 90 s, then a repair retry waited another 90 s. Now: `.env` PITCHMIRROR_ANALYSIS_MODEL=gemini-3.5-flash
(~7 s per call in a benchmark), per-model timeout 40 s (`POST_AGENT_TIMEOUT_S`), and `_run_single_agent` re-runs a
failed/timed-out/overloaded agent on `PITCHMIRROR_FALLBACK_MODELS` (default gemini-3.5-flash, gemini-2.5-flash,
gemini-3.1-flash-lite). No repair retry when every model already failed. End-to-end: report ready ~37 s after stop.

## Added: glass bubble, live answer helper, question-by-question review (+ faster report)

- Bubble (`meeting.html` template, `meeting.css` "glass" section, `meeting.js`): translucent blurred card, slim-strip
  mode (▁/▢ button; PiP window resized to fit; remembered), answer-outline panel, last 3 tips, talk-share stat.
  A Document-PiP window can't be see-through over other apps (browser limit) — it gets a dark backdrop; the in-page
  floating bubble is truly translucent and fades when idle.
- Live answer helper: a "Them" segment that `looks_like_question` → `_request_answer_hint` → 3-point outline via
  `PITCHMIRROR_HINT_MODEL` (default gemini-3.1-flash-lite, usually <1 s; hedged second request after 2.5 s; falls back
  to the live model) → `answer_hint` WS event; stored in `answer_hints`.
- Q&A review: `extract_qa_pairs` (Them question → your answer) + `QA_REVIEW_AGENT` (runs in parallel with the
  analysts) → `qa_review` [{question, answer, rating 1-5, verdict, worked, improve, better_answer, hint_points}],
  shown on the review page and in the .txt download.
- Report speed: analysis model now gemini-2.5-flash, research gemini-3.1-flash-lite; profile/memory update runs after
  the scorecard is sent. Scripted meeting: review ready ~40 s after End.
- HTML pages are served with `Cache-Control: no-cache`; static files are cache-busted with `?v=` (bump on changes).

## Switched text work to Claude (Anthropic API, Max-plan credits)

- `backend/claude_client.py`: `report_text` (Claude Opus 5.5, adaptive thinking, effort medium, server-side refusal
  fallback `fallbacks="default"`) and `fast_text` (Claude Haiku 5.5, thinking off, effort low). Enabled when
  `ANTHROPIC_API_KEY` is set; the key is a user key, so `ANTHROPIC_WORKSPACE_ID` is sent as `anthropic-workspace-id`.
- Opus 5.5: all post-session agents (`_run_single_agent` tries Claude first, then the Gemini model chain).
- Haiku 5.5: live answer outlines (`_hint_via_text_model`) and meeting content checks (`_run_content_check`).
- Gemini unchanged for: live transcription, practice voice coach; and fallback for everything above.
- Measured on a scripted meeting: outline 1.6 s; report ready 54 s after End; ~15.7k input / ~7.9k output Opus
  tokens ≈ $0.22 per session. Max 5x credit is $100/month (expires monthly, no rollover).

## Phase 1 (pilot demo) — 2026-10-20

- **Pre-call brief** (`backend/meeting_brief.py`, Meeting Mode setup "Pre-call brief"): notes + points to cover +
  planned length, sent as a WS `brief` message (allowed up to 24 KB). No points → Claude Haiku derives them from
  notes. Live coverage via Haiku every 30 s (+ a final pass before the recap); `brief_points` WS updates;
  `brief_reminder` at 80% of planned length. Brief also feeds answer outlines and conversation checks.
- **After-call recap** (`RECAP_AGENT`, Opus, meeting only, parallel with analysts): summary, decisions, action items,
  open questions, client signals, follow-up email → `scorecard.recap`; shown at the top of the review with
  Copy / Open-in-mail buttons; included in the .txt download.
- **Pilot feedback**: form under every review → `POST /api/feedback` (SQLite `feedback` table / Firestore
  `pitchmirror_feedback`); `/pilot` page aggregates all testers (avg rating, would-use %, comments, CSV, print).
  `/api/feedback/summary` is unauthenticated — put it behind admin auth before any shared deployment.
- **Tester picker** (`frontend/tester.js|css`, loaded before app/meeting scripts): per-tester user ids in
  localStorage (`pitchmirror_testers`), header menu to switch/add/rename.
- **Demo guide**: `/guide` (printable). Linked from landing nav, Meeting Mode header and the tester menu.
- Verified end to end with synthesized voices: brief coverage 3/4 (correct), recap/email accurate, Q&A 4/5,
  review ~55 s after End; feedback form → /pilot summary in the browser. Test data removed afterwards.

## Bubble upgrades — 2026-10-20

- **Help buttons** (bubble): `help` WS message → `_request_help`: "answer" reuses the answer-outline path for the
  last question from Them; "catchup" / "ask" → Claude Haiku → `help_reply` {kind, title, lines}. 3 s min gap per kind.
- **Client mood**: `_run_mood_check` every ~30 s with ≥12 new Them words → `client_mood` {mood, reason, alert};
  alert only when the mood changes to confused / skeptical / disengaged. Stored in `client_moods`, shown in the recap.
- **Answer timer / interruptions** (browser, from per-chunk RMS of both streams): answer chip + wrap-up nudge at 2 min;
  talking over Them ≥0.8 s → `interruption` WS message → `interruption` event (silent record if cooldown).
  Assumes headphones (speaker echo would look like you talking).
- **Trust & smart quiet**: You/Them level meters; tips held while Them is talking (THEM_HOLD_MS) and shown on pause;
  👍/👎 per tip → `nudge_feedback` → `CoachingEvent.user_rating` → review + `/pilot` "Live tips" table.
- **Controls & agenda bar**: ⚙ opacity / size S-M-L (CSS zoom) / dim when idle / hold tips toggle (localStorage
  `pitchmirror_bubble_prefs`); agenda bar = time used vs planned + points covered (amber at 80% with points left).
- Verified end to end (help ×3, mood, 👍 stored, interruption counted, coverage 3/4) and visually (full + strip).

## Transcription quality, step 1 — 2026-10-20

- **Audio levelling** (`frontend/audio-leveler.js`, used for the practice mic, the meeting mic and meeting audio):
  85 Hz high-pass → AudioWorklet leveller (smooth AGC toward a target speech RMS, max ×6 mic / ×5 meeting,
  min ×0.35, no boost below the noise floor or in pauses) → soft limiter. Measured in Node: whisper (−34 dB) ×4.8,
  normal unchanged, very loud ÷3 without clipping, hiss not boosted. Mic also requests browser
  `autoGainControl` + `noiseSuppression`. Worklet chunks are `{pcm, raw, gain}` — voice logic uses `raw`.
- **Transcriber handovers** (`backend/transcriber.py`): audio sent since the last returned segment is kept
  (`_unconfirmed`, 30 s) and replayed into the next session after any handover or connection error.
  Planned rollover from 8.5 min, right after a segment returned, during a pause, with ≤4 s unconfirmed;
  forced at 9.5 min. Watchdog: ≥40 s voiced and no text for 60 s → restart + replay (`stt_status` events,
  bubble shows "Re-syncing … transcript"). Per-stream stats saved as `scorecard.transcription_health`.
  Note: gemini-3.5-transcribe-live only returns text after a ~2 s pause.
- Research notes: non-live `gemini-3.5-transcribe` returns text in `parts[].audioTranscription.text` (SDK doesn't
  parse it), missed a −32 dB sentence the live model caught, and hit the free-tier quota after ~4 calls — not
  used. Free alternatives considered for later: faster-whisper / WhisperLive (CPU on this i7 is slow for
  medium/large), RNNoise / DTLN / DeepFilterNet3 AudioWorklet noise suppression, Silero VAD (check licence).

## Full test pass — 2026-10-20

- 69 unit tests, JS syntax, every page/static/API (200), bad inputs rejected (400/404), Claude Haiku + Opus and
  Gemini live models reachable.
- Practice e2e found: the live model often logs an issue (or nothing) without speaking. Fixed with
  `_practice_voice_followup`: measured filler / pace checks every 3 s (like Meeting Mode) and, when no coach audio
  followed a confirmed issue within 2 s, a `coach_cue` with `speak: true` → the browser says it with
  `speechSynthesis` (mic chunks are dropped while it speaks so the cue isn't transcribed). Asking the live model
  to speak via `send_content` didn't work — it checks tools instead (system prompt says measure first).
- Meeting e2e: outlines, coverage 3/4, tone/pace nudges, mood, interruption recorded, help ×3, recap + email,
  Q&A 3-4/5, ratings — all OK. Review ~55-60 s after End. Pages load without app errors (MediaPipe "INFO" console
  lines are expected).

## Notch redesign of the meeting bubble — 2026-10-20

- Bubble styles moved to `frontend/bubble.css` (removed from meeting.css; PiP window injects bubble.css).
- Structure: `.pm-pill` (status dot by `data-state`, `.pm-mini` one-line status/tip, `.pm-ring` SVG progress =
  time used vs planned or points covered, timer, ⌄ details) + `.pm-drop` card (message with 👍/👎, answer outline,
  details drawer, settings, action row: 💡 Answer · ↺ Catch up · ? Ask · ⚙ ▁ ❚❚ ■). All old `pm-*` hooks kept.
- Behaviour: `data-open` opens on a tip/outline/help and folds back (`settleToListening`, tips 9 s, outlines 25 s);
  tap the pill to peek (drag-safe), ⌄ toggles `data-details`; ▁ = pill only. In-page fallback starts top-centre.
  In the PiP window the card stays open (a PiP window can't resize itself or dock to the screen edge).

## Live notch test (one-sided, Teams) + fix — 2026-10-20

- Real test: 185 words / 3.5 min transcribed on one session, no stalls; 6 relevant nudges (eye contact ×2, flat
  tone ×2, fillers, rambling ×2); Catch-me-up worked; review + recap generated.
- Fix: the client-audio transcriber now starts lazily on the first meeting-audio frame (`state.other_stt_task`),
  instead of idling (Google closed the idle session twice). Awaited (≤8 s) after the live phase so its last
  segment is kept. Verified: one-sided meeting starts no client transcriber; two-sided still transcribes both.
- Fix (user report): End button was clipped at the card's right edge (action row wider than the card).
  Action row is now two groups (`.pm-help-group`, `.pm-ctl-group`) that wrap onto two lines when needed;
  End is a labelled red pill (`■ End`). Verified fully visible at S/M/L, PiP width and a 330 px card.

## Meeting + practice extras — 2026-10-21

Nine features from the "what more can we add" list. Helpers (prompts, parsers, calculations) live in the new
`backend/coach_extras.py`; wiring is in `backend/main.py`. Tests: `tests/test_extras.py` (78 total passing).

**Meeting Mode**
- **Talk time / monologue.** The browser measures voice activity (100 ms chunks). Holding the floor past the
  mode's limit (sales/general 90 s, pitch/interview 120 s, none for presentations) sends
  `{type:"talk_time", kind:"monologue"}`; over 75 % of the talking in the last 5 min (sales/general, meeting audio
  shared) sends `kind:"talk_share"`. The server flags `monologue` / `talk_share` through the normal cooldowns
  (`_record_talk_time`) and cues back. `kind:"stats"` every 15 s → `talk_stats` (you/them seconds, share,
  longest stretch) shown in the recap. Replaces the old local 2-minute "wrap up" nudge.
- **Commitments tracker.** Every finished line that matches `PROMISE_RE` gets a Haiku check
  (`_extract_commitments`; sentence kept as-is if no text model). New `commitments` WS message → page list +
  notch row "Promised:" + a "🤝 Noted" nudge. Fed into the recap agent (action items + follow-up email).
- **Past-call memory.** Brief has a "Who is it with?" field (autocomplete from `/api/clients`). Setup page shows
  the last call with that client (`/api/clients/last`: summary, still-open questions, their concerns, your
  promises). During the call the server loads it (`previous_call` WS message → hint card) and every live helper
  and the recap get it via `_brief_block()`. Sessions now save `client`.
- **Pre-call rehearsal.** "Rehearse this call" in the brief: `/api/rehearsal/questions` (3 hardest questions
  from the brief + last call; fixed fallbacks without a model), read aloud with the browser voice, answers
  transcribed through a new transcription-only socket `/ws?kind=rehearsal` (nothing saved), then
  `/api/rehearsal/review` (QA reviewer prompt) → side-by-side "You said / Stronger answer" cards.
  Echo of the question through laptop speakers is filtered by keyword overlap.
- **Export the recap.** Review page: Copy for Notion/docs (Markdown), Download .md, Email follow-up, and
  "Send to <label>" when `PITCHMIRROR_EXPORT_WEBHOOK_URL` is set (POSTs the recap JSON — Zapier/Make/CRM hook;
  label from `PITCHMIRROR_EXPORT_WEBHOOK_LABEL`). Not set yet.

**Practice app**
- **Progress over time.** History opens with four small line charts (fillers/min, wpm, look-aways/min, score),
  filter Practice / Meetings / All, hover tooltip, table view, click a point to open that session
  (`/api/sessions/trends`; meeting pace uses your own talking time).
- **Practice goals.** `/api/goals` (new SQLite `goals` table): presets or custom metric + target, weekly progress
  bars (Monday-start), goal line on the charts, and a "Goals" pass/fail row under each new score.
- **Replay with markers.** Timeline markers are clickable (and keyboard-focusable); the scrubber and markers
  scroll the review transcript to that moment and flash the line. Commitments appear as green markers.
- **Side-by-side rewrite.** Meetings: the Q&A review shows your answer next to the stronger one. Practice: new
  `REWRITE_AGENT` picks 2–3 weakest passages ("Say it better"). Both have 🔊 Read it aloud and 🎙 Retry it
  (records via the rehearsal socket; instant words / wpm / fillers / key-words-used check, no AI call).

**Other fixes in passing:** `/api/sessions/trends` must be registered before `/api/sessions/{session_id}`;
`escapeHtml` in app.js now escapes quotes (it is used inside attributes).

**Not yet tested live:** a real call with commitments/talk-share/previous-call, a spoken rehearsal and a retry
(the in-app browser has no mic — test in Chrome). Cost: rehearsal ≈ 2 Haiku calls; commitments ≈ 1 Haiku call
per promise-like line; rewrites 1 report call per practice session.

## Renamed to Pitchlight — 2026-10-21

All user-visible "PitchMirror" text → "Pitchlight": page titles and logos, landing page (mock URL now
pitchlight.ai), the live coach's and report writer's self-name, review/feedback download names
(`pitchlight-session-*.txt`, `pitchlight-pilot-feedback-*.csv`), export webhook `source`, code headers.
Internal identifiers unchanged on purpose (see CLAUDE.md "Name"). Static files bumped to `?v=20261021b`.
Not renamed: the product films in `video/` (narration and captures still say PitchMirror) and README/PLAN/docs
from the hackathon.

## Stumbles + setup chatter (after two live tests) — 2026-10-21

From the first two live tests (practice 3.5 min, Teams meeting 2.5 min; both ran clean, report + rewrites + help OK):
- **Repeated words count as stumbles.** `count_stumbles()` in `session_state.py` finds a word or phrase of up
  to 6 words said twice in a row ("it it", "Chief OS Chief OS", "where you can see where you can see");
  normal doubles (very very, bye bye, no no) and numbers are excluded. `scored_filler_count()` = fillers +
  stumbles; the filler card shows "N detected (M repeated words)" and the report agents get the split.
  On the real practice transcript: 0 fillers → 9 genuine repeats, no false positives.
- **Setup chatter is not scored.** `trim_setup_chatter()` runs when the session ends, before analysis: within
  the first/last 20 s of the presenter's speech it drops lines in the non-dominant script (Hindi asides in an
  English pitch), trims such runs off the edge of a line, and drops short start/stop/test phrases
  ("start bolna start listen"). Stops at the first real line on each side; mid-talk lines and the other side
  are untouched. Removed text is saved as `ignored_chatter` and shown above the review transcript.
- Live notch/meeting counters are unchanged (they still count only fillers while you speak).
