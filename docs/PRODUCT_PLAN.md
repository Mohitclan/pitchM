# Pitchlight — product plan: "a beginner can just go and present"

Written 2026-10-22 after inspecting the whole product and researching what people get stuck on.

## The goal
A complete beginner opens Pitchlight, gets ready in minutes, and then goes into an interview, a client call or a
presentation **knowing what to say, where they are, and what to do when they freeze** — without having
prepared for days.

## What the product does today (inspected)
- **Practice app:** live transcript, live stats, key-point checklist, advice cards, "Say it better", AI role-play
  investor/interviewer/client, pitch versions, video replay, progress charts and goals, full review.
- **Meeting Mode:** floating coach bar, separate Answers box (Answer / Catch up / Ask / Earlier questions),
  facts card, commitments, objection cards from prepared answers, talk-time, next-move advice, recap + follow-up
  email, past-call memory per client.
- **Accounts:** each person signs in and has private data.

**What it does NOT do (checked in the code):** no script or teleprompter, nothing that follows you through a
script, no "I'm lost" rescue, no live structure frame (STAR etc. appear only in the after-call review), no
help writing the pitch in the first place, no answers grounded in the user's own documents, no jargon warning,
no "am I ready?" check, nothing specific for Hindi/Hinglish speakers.

So today the product is strong at **measuring and reacting**. A beginner's biggest problem is earlier: they
**don't know what to say**.

## The blockers beginners hit (evidence)
| # | Blocker | Evidence | Solved today? |
|---|---|---|---|
| 1 | **Forgetting what to say / freezing / losing the thread** | UK survey of 2,007 people (Preply): forgetting words 36%, freezing 33%, looking foolish 33%, losing train of thought 28% | No |
| 2 | **Blank page: "what do I even prepare?"** | Beginners find the opening hardest and struggle to speak from notes (university presentation handbooks) | No |
| 3 | **Rambling, no structure, no result** | Interview coaching sources: unstructured answers, missing "Result", vague actions | After the fact only |
| 4 | **Unprepared questions and made-up answers** | VC and sales sources: founders skip the hard questions; answers invented under pressure | Partly (outlines use no user documents) |
| 5 | **Too many numbers, too much feedback** | Existing coaching apps measure many things but give no drills | Our scorecard is metric-heavy |
| 6 | **Remote calls: eyes on notes, can't read the room** | Presenters look at faces instead of the camera; can't see audience while sharing | Partly (gaze, mood chip) |
| 7 | **Language: thinking in Hindi, speaking English** | Our own live tests mixed Hindi and English | Untested for feedback |
| 8 | **"Am I good enough to go?"** | Beginners can't judge their own readiness | No |

Source quality: only the Preply survey is quantitative; the rest is practitioner and vendor material, so treat it
as direction, not proof. We should measure our own users.

## The beginner path we want
1. **Prepare me** → paste your material → get a script, key points, hard questions with answers.
2. **Rehearse** against the AI investor / interviewer / client (exists) with the script on screen.
3. **Ready?** → one rehearsal gives "ready" or "two things to fix".
4. **Go live** → script follows your voice; Answers box holds answers from *your* documents; "I'm lost" brings you back.
5. **After** → recap + ONE thing to fix next time, with a 2-minute drill.

## What to build, in order
### Tier 1 — the core ("Prepare → Follow → Ground")
| Feature | What it does | Effort |
|---|---|---|
| **A. Prepare Me** | Paste or upload product page, deck text, job description, CV, notes. Claude drafts: opening, 4–6 key points in order, the closing ask, the 5 hardest questions with model answers, a one-page cheat sheet. Edit, then save as a named script. | ~2 days |
| **B. Script follow-along + "I'm lost"** | The script appears in a **Script tab of the Answers box**. As you speak the current line highlights (matched on your words, with a cheap AI re-check if it loses you). Big text near the camera. **"I'm lost"** shows where you are and the exact next sentence. Skipped sections are flagged. | ~3–4 days |
| **C. Your own facts library** | Upload pricing, FAQ, product sheets once. Answer outlines and objection cards use and cite them. If it isn't in your material: "Not in your notes — say you'll confirm and follow up." No invented claims. | ~2–3 days |

### Tier 2 — make it easy to get right
| Feature | What it does | Effort |
|---|---|---|
| **D. Structure frames, live** | Pick a frame (STAR, problem→solution→proof→ask, elevator, opening). A live checklist shows which parts you've covered and nudges: "You gave the situation and action — now the result." Opening coach for the first 30 seconds. | ~2 days |
| **E. Beginner mode + one thing to fix** | At most one tip every 30 s and only the top two issues; plain-language scorecard; after each session ONE focus and a 2-minute drill; progress and wins highlighted. | ~2 days |
| **F. Readiness check** | Before a real call: one rehearsal against the script → "Ready" or "Two things to fix", with a tick list (points hit, timing, fillers, opening). | ~1 day |
| **G. Hindi / Hinglish** | Test and tune transcription and feedback for mixed Hindi–English; language setting for tips and scripts. | ~2–3 days |
| **H. Plain-language nudge** | Flags jargon and acronyms with a simpler way to say it. | ~1 day |

### Tier 3 — later
Nerves toolkit (60-second warm-up, breathing cue when pace spikes), interview story bank (CV → STAR stories per
competency), team templates and shared scripts, manager view of practice activity.

## What not to build
More metrics, bigger dashboards, or anything that adds clutter during a live call. The product should say less
and help more.

## How we'll know it works
- A first-time user reaches a finished rehearsal in **under 10 minutes**.
- **Key points hit** per rehearsal goes up across versions.
- Tips shown per minute stays low while the "was it useful" rating stays high.
- Users say they would use it in a real meeting (pilot feedback page).
- Repeat use: people come back for a second and third rehearsal before real calls.

## Recommended first release (about 1.5–2 weeks)
**A + B + C** with **F**: Prepare Me, the script that follows you with "I'm lost", answers grounded in your own
documents, and a readiness check. That is the point where "just go and present" becomes true.

## Decisions needed
1. **Main user first:** interview candidates, sales/client calls, or general presenters? (Changes which frames and
   templates ship first.)
2. **Languages:** English only for now, or Hindi/Hinglish from the start?
3. **Material:** is it fine for users' uploaded documents to be sent to Claude to draft scripts and answers?
