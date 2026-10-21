# Script — Product Walkthrough (90 s) · `ProductWalkthrough90`

Same narrator as the 60 s film: Kokoro `af_heart`, speed 0.92. The narration is 63 s long.

This version shows how each part works, with real clicks and real states, rather than repeating the overview more slowly.

Narration text lives in `tts/lines.json` and timings in `engine/comps.js`.

| Time | Narration (start time) | On screen |
|------|------------------------|-----------|
| 0:00–0:06 | (0.9 s) "This is Pitch Mirror. A speaking coach that's with you while you talk, not after." | Wordmark and **Coached while you speak.** |
| 0:06–0:15 | (6.3 s) "Most feedback arrives too late. Pitch Mirror listens live, measures how you speak, and tells you one thing to fix, right when it matters." | "Measured live, while you talk", then chips **Fillers · Pace · Eye contact**, arriving with "measures how you speak". |
| 0:15–0:25 | (15.5 s) "Start in the practice app. Pick your coach, what you're preparing for, and the one thing you want to improve." | Real Session Intake. The camera pushes into the intake. The cursor sets **Brutal VC**, **Pitch** and **Improve pacing**, then clicks **Start Session**. Label: **Pick your coach and goal**. |
| 0:25–0:38 | (25.6 s) "As you speak, your words appear on the left. Coaching moments appear on the right: what was measured, and how to fix it." | Live session. Push to the transcript (label **Live transcript**), then to Coach Suggestions with the Pace card ("184 wpm, threshold >170", "Coach said…", "Try this"). Label: **What was measured, and how to fix it**. |
| 0:38–0:50 | (38.6 s) "For real calls, there's Meeting Mode. Add a short brief, and the points you must cover. Then share the meeting tab, so the coach hears both sides." | `/meeting`: open **Pre-call brief**, type the notes and three points, set **30 min**, click **Choose meeting audio** ("Connected: Meeting tab ✓"), then **Start meeting coach**. |
| 0:50–1:04 | (50.4 s) "A small notch sits at the top of your screen. When the client asks a question, it drops an answer outline. It ticks off your points, and flags fillers, and only you can see it." | Push into the notch: resting pill (0/3 points), the client's question, the 💡 Answer outline with 3 points, the pill back at 1/3 points, a Nudge and a Heads up. Then the cursor taps the pill and clicks **? Ask**, and "Question to ask" appears. Labels: **Answer outline**, **Points & nudges**, **Help on demand**. |
| 1:04–1:18 | (64.4 s) "When the call ends, everything is waiting. A recap, with decisions and action items, and a follow-up email you can copy in one click." | Review: Meeting recap (points covered, client mood, decisions, action items), then the Follow-up email. The cursor clicks **Copy** and "Copied ✓" appears. |
| 1:18–1:26 | (78.1 s) "Every question you were asked gets a rating, and a stronger sample answer. And the full transcript is yours to download." | Question-by-question review with ratings and stronger answers, then Transcript + Coach Review side by side, with **Download .txt** highlighted. |
| 1:26–1:30 | (86.6 s) "Pitch Mirror. Speak like you rehearsed." | End card: wordmark, tagline, **Try the PitchMirror pilot**. |

**Where each claim comes from in the code**

| Claim | Source |
|-------|--------|
| "copy in one click" | The **Copy** button calls `copyRecapEmail()` in `app.js` |
| "download" | `downloadSessionReview()` saves a .txt file |
| "hears both sides" | **Choose meeting audio** uses `getDisplayMedia` tab audio, and the backend runs a second transcriber |
| "ticks off your points" | `brief_points` coverage checks |
