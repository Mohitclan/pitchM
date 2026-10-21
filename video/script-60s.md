# Script — Product Overview (60 s) · `ProductOverview60`

**Narrator:** Kokoro `af_heart` at speed 0.92, generated locally. The narration is 35.5 s long, which leaves calm pauses between lines.

**Source of truth:** narration text lives in `tts/lines.json` and timings in `engine/comps.js`. This page mirrors both. "PitchMirror" is written as "Pitch Mirror" in the TTS text so Kokoro pronounces it as two words.

| Time | Narration (start time) | On screen |
|------|------------------------|-----------|
| 0:00–0:05 | (1.2 s) "You know exactly what you want to say." | Off-white field. **You know what to say.** |
| 0:05–0:12 | (5.3 s) "Then the fillers creep in. You rush. You lose the camera. And nobody tells you, until it's over." | Words timed to the voice: **Um.** → **Too fast.** → **Eyes down.** → **Nobody tells you.** |
| 0:12–0:15 | (12.3 s) "Meet Pitch Mirror. An AI speaking coach that listens while you talk." | ◎ PitchMirror wordmark and the line "An AI speaking coach that listens while you talk." A soft whoosh plays. |
| 0:15–0:31 | (18.2 s) "Rehearse a pitch, or an interview. Your words appear live, and the coach steps in only when it measures a problem." | Real Practice app in a browser frame. The cursor clicks **Start Session**, the live transcript fills, and the Filler words card lands, then the Pace card. The camera pushes into Coach Suggestions. Label: **Measured, not guessed · 184 wpm over the last 20 seconds**. |
| 0:31–0:43 | (31.2 s) "Then, take it into the real call. A small notch sits above Zoom, Google Meet, or Teams, with silent nudges only you can see." | Real Meeting Mode page. The camera pushes into the notch: resting pill, then the client's question, the 💡 Answer outline, a Nudge, and a Heads up. Label: **Silent nudges · Text only. Nobody else in the call sees them.** |
| 0:43–0:54 | (43.5 s) "When you finish, you get a scored review, every question with a stronger answer, and a follow-up email, ready to send." | Three crossfades: Session Scorecard, Question-by-question review, Follow-up email. Each has a slow push and a label. |
| 0:54–1:00 | (55.0 s) "Pitch Mirror. Speak like you rehearsed." | End card: wordmark, **Speak like you rehearsed.**, and **Try the PitchMirror pilot**. Fades out. |

**Where each claim comes from in the code**

| Claim | Source |
|-------|--------|
| "only when it measures a problem" | Measured gates in `backend/agents/tools.py` (`server_flag_issue`, cooldowns) |
| "Zoom, Google Meet, or Teams" | Platform options on `/meeting`; the notch is an always-on-top browser window |
| "silent nudges" | Meeting mode forwards no coach audio (`backend/main.py`) |
| "every question with a stronger answer" | `QA_REVIEW_AGENT` |
| "follow-up email" | `RECAP_AGENT`, plus the Copy and Open in mail app buttons |
