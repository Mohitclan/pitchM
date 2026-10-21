# Pitchlight — demo guide (script and flow)

> **Tested for real on 2026-10-21.** Both demos below were run end to end against the live app with spoken audio
> (client voice + your voice streamed into the real server): one Meeting Mode call (twice) and one role-play
> practice session. The timings and "what you'll see" columns come from those runs.

Two demos, about 6 minutes each. Run them in this order: **Meeting Mode first** (the wow factor), then the **Practice app**.

## Before you go on (10 minutes, once)

1. Start the stack: `cd /Users/admin/Desktop/hub/GeminiLiveAgentHack && docker compose up -d --build`, then check http://localhost:8080/health says ok.
2. Use **Chrome** (not the in-app browser) and **wear headphones**.
3. In Chrome allow **microphone** and **camera** for localhost:8080.
4. Open the tabs you need:
   - Tab A: http://localhost:8080/meeting
   - Tab B: `demo/demo-client.html` (the client "Priya" — five buttons that play her lines). Open it with *File → Open File* or drag it into Chrome.
   - Tab C: http://localhost:8080/app (for the second demo)
5. Pick "Who's testing?" (top right) → a fresh name such as **Demo**, so the History starts clean.
6. Do one dry run of the meeting's first two minutes so the mic/camera permissions and the face model have loaded (the first face-model load takes about 15 seconds).

Cost of a full run: roughly $0.30 in Claude credits for the meeting and about the same for practice. Gemini live voice is free on your key.

---

# Demo 1 — Meeting Mode ("a coach in your ear during a real call")

**One-line pitch to say first:** "On a sales call you're doing five things at once. Pitchlight sits in a small floating bar that only you can see, listens to both sides, and quietly tells you what to do next."

## Setup on screen (60 seconds)

On http://localhost:8080/meeting:

| Field | Enter |
|---|---|
| What's the meeting? | Sales call / demo |
| Where is it? | Microsoft Teams |
| How chatty? | Active (shows more of the coach for the demo; use Balanced in real calls) |
| Camera | ticked |
| Who is it with? | **Northwind Dental** |
| Context notes | Priya, head of operations, 40 clinics. They use spreadsheets and Zoho today. Goal: agree a 30-day pilot on five clinics. |
| Points to cover | Billing Hub centralizes payments / Stripe and Razorpay routing / Offer a 30 day pilot / Agree the next meeting date |
| Prepared answers to objections | `Price: pays back in about three months for a forty clinic chain` and `Competitor: we plug into Zoho, so there is nothing to replace` (one per line) |
| Planned length | 15 min |

Then click **Choose meeting audio** → pick the **demo-client tab** (Tab B) → tick **Share tab audio**. The page should say "Connected". Click **Start meeting coach**, allow the mic, and drag the floating bar to the top of your screen.

Say to the audience: "This bar floats over Zoom, Teams or Meet. Nobody on the call can see it."

## The call — what you say, and what Priya says

Press the matching button in the client tab at each **▶ cue**. Speak naturally; the script is a guide.

| # | Who | Line | What the audience should see |
|---|---|---|---|
| 1 | ▶ Priya (button 1) | "Hi, thanks for making the time. I'm Priya, head of operations at Northwind Dental. We run forty clinics, and we want to go live by March." | Within ~20 s the **Facts card** fills in: *Priya, Head of Operations · runs 40 clinics · wants to go live by March*. About 20–35 s in, a **Next move** tip appears (e.g. "Ask who else signs off…") |
| 2 | **You** | "Thanks Priya, great to meet you. So, um, basically Billing Hub is, uh, a centralized payments platform. It routes every payment through Stripe or Razorpay, so each clinic gets paid the right way, you know, wherever the patient is." *(look down at your notes for 6 seconds while you say it)* | An **eye-contact** nudge after ~5 s of looking down, a **filler** nudge ("4 filler words in 30 s"), points ticking |
| 3 | ▶ Priya (button 2) | "How long does setup take for forty clinics?" | An **answer outline** appears in the bar within 1–2 s (three short points). Click **Answer** for a second one |
| 4 | **You** | "About two weeks. One week to import your data, and one week of staff training. I'll send you the rollout plan by Friday." | "**Noted: send the rollout plan (Friday)**" — your promise is tracked |
| 5 | ▶ Priya (button 3) | "Honestly, that seems too expensive for us right now. We already use Zoho for our billing." | An **objection card** (🛡 Price) with *your prepared answer* — "pays back in about three months…" — plus three steps: acknowledge, answer, ask back. Her second sentence (Zoho) gets its own card. The client-mood chip turns **Skeptical** |
| 6 | **You** | "That's fair. We plug into Zoho, so there is nothing to replace, and it pays back in about three months. What does your billing process look like today, and how many people touch it?" | Question counter goes up |
| 7 | ▶ Priya (button 4) | "Three people reconcile spreadsheets every week, and it takes about two days. We will share our clinic list by next Tuesday." | Facts: 3 people, 2 days; their promise is noted |
| 8 | **You** | Click **Ask** in the bar — it suggests a question like "Would a 30-day pilot with five clinics help you judge fit?" Say it aloud. Then: "That is a lot of time. Let's start with a thirty day pilot on five clinics. Can we book a call next Wednesday to agree the start date?" | Points 3 and 4 tick: 4/4 |
| 9 | ▶ Priya (button 5) | "Yes, Wednesday works. Send me an invite." | |
| 10 | **You** | "Perfect. I'll send the invite today. Thank you, Priya." Then click **End** in the bar. | "Analyzing…" |

While it analyses (**about 70 seconds**), say: "It's now writing the recap, the follow-up email and a review of every question I was asked."

## After the call — what to open and say

1. **Recap**: summary, decisions, **commitments (yours and theirs)**, **key facts**, **talk time** bar, **action items**.
2. **Follow-up email**, ready to send, using her real name and numbers — click **Copy** or **Email follow-up**.
3. **Export row**: **Copy for Notion / docs**, **Download .md**.
4. **Question-by-question review**: her question, your answer, a rating, and a **stronger version** with 🔊 *Read it aloud* and 🎙 *Retry it*.
5. **Transcript** with coaching markers. Click a marker to jump to that moment.
6. Next call with the same client: go back to /meeting, type **Northwind Dental** in "Who is it with?" and show the card that appears: *"Last call… You promised… Still open…"*.

**Closing line:** "Everything stays on this laptop, and the coach never speaks into the call."

### Settings worth a mention

- **How chatty?**: Quiet (delivery flags only), Balanced (adds next moves and objection cards), Active.
- **Rehearse this call** (in the brief): the AI plays the client and asks the 3 hardest questions before you go in.
- Smart quiet holds a tip while the client is talking and shows it when they pause.

---

# Demo 2 — Practice app ("rehearse against an AI investor")

**One-line pitch to say first:** "Before the real meeting, you can rehearse the pitch against an AI investor who asks follow-up questions — and get live coaching on the side."

## Setup on screen (45 seconds)

On http://localhost:8080/app:

| Field | Choose |
|---|---|
| Who is your coach today? | Professional Coach |
| What are you preparing for? | Pitch |
| Where will you deliver this? | Virtual |
| Primary goal | Balanced improvement |
| **Practice with** | **Role-play: an investor asks questions** |
| **Feedback** | **Live** |
| **Pitch name** | **Billing Hub pitch** |
| **Key points to hit** | Billing Hub powers payments for 30+ ventures / Stripe and Razorpay routing by country / One dashboard with churn tracking |
| Record video for replay | ticked |

Click **Start Session**.

## The run

| # | Who | Line | What the audience should see |
|---|---|---|---|
| 1 | **Investor** (it speaks first) | Says something like "Hello, I'm ready, please start when you are." | You *hear* it, and the same words appear in the transcript labelled **Investor** |
| 2 | **You** | "Okay, ready, let's start." *(this is setup chatter — it won't be scored)* | |
| 3 | **You** | "Hi everyone, I'm Poorab. Today I'm pitching Billing Hub. So, um, Billing Hub is a centralized payments platform that powers payments for more than thirty ventures. It works on two routes: Stripe for users in the US, and Razorpay for users in India." *(glance down at notes for 6 seconds)* | **Live panel on the right:** pace, fillers, repeats, eye contact (turns amber → "looking down"); key point **✓ 1** ticks within ~15 s, **✓ 2** a little later; an **advice card** such as "Lead with the traction claim…"; an **eye-contact** card |
| 4 | **Investor** | Reacts and asks a follow-up, e.g. "30 ventures is impressive. Are these startups, established companies, or something else?" | It speaks **and** types its question. **Answer what it asks** — the review rates your answers against its questions |
| 5 | **You** | Answer its question, then add: "Every payment goes through one dashboard where you can see all revenue in real time. We are adding churn tracking next quarter." | Point 3 ticks (**3/3**); another **advice card** |
| 6 | **You** | Click **✨ Say it better** | Within ~3 s: your last lines rewritten stronger ("Thirty ventures are live on Billing Hub today…") |
| 7 | **Investor** | Pushes back, e.g. "Routing by country sounds complex. How does that integrate with Stripe and Razorpay?" | |
| 8 | **You** | "Our pricing is a small percentage per transaction, and it replaces three separate integrations for every venture. Thank you." Click **End Session**. | |

## After the session — what to open and say

(The report takes **about 50 seconds**.)

1. **Score** and category cards (the filler card says how many were **repeated words**).
2. Under the transcript: **"Not scored (setup chatter)"** line.
3. **Pitch versions**: *Billing Hub pitch — version 1*, key points hit. Run it once more and show **v2 vs v1** (score, length, fillers, key points).
4. **Video replay**: your own recording; click **Jump to** a flagged moment. Say: "The video is stored only in this browser."
5. **Say it better**: weakest passages rewritten, with 🔊 and 🎙 *Retry it*.
6. **Question-by-question review**: the investor's questions with ratings and stronger answers.
7. **History** → **Your progress** charts and **Practice goals** (add "Under 3 fillers a minute").

**Closing line:** "Rehearse here, then carry the same coach into the real call."

---

## What the live tests proved (so you can say it with confidence)

- Both voices are transcribed in real time; the client's lines are labelled **Them**.
- The facts card, commitments (yours **and** theirs), objection cards with your prepared answer, answer outlines,
  talk-time nudge, client-mood arc and key-point coverage all fired during the call.
- **Past-call memory works:** the second call with *Northwind Dental* showed last time's promises and open
  questions within a second of starting, and the live tips used them ("Ask which five clinics would make the best pilot").
- The role-play investor **speaks and writes**, asks real follow-ups, and the review rates your answers honestly
  (a 2/5 and a 1/5 when the test answers ignored its questions).
- Setup chatter ("Okay, ready, let's start") is left out of the score and listed as "Not scored".

## Not covered by the automated tests (check once yourself before the demo)

- **Camera-based features**: eye-contact tracking in your own browser and the **video replay** (needs your real
  webcam; the test drove the server directly). Do a 30-second practice run and confirm the Video replay card appears.
- **Sessions longer than ~15 minutes**, and a real call with another person's voice and room noise.
- **Next-step guard** (fires near the end of a planned-length call) and the **question coach** (5 minutes with no
  open question) — covered by unit tests only.

## If something goes wrong on stage

| Problem | Fix |
|---|---|
| No mic / "blocked" | Click the lock icon in the address bar → allow microphone → reload |
| Meeting says "Not connected" for client audio | Re-click **Choose meeting audio**; make sure you picked the **tab** and ticked **Share tab audio** |
| Nothing from the investor | Wait ~5 seconds after Start; check headphones are on |
| Transcript empty | Speak a full sentence, pause for 2 seconds (it appears after short pauses) |
| Review says "Analysis incomplete" | A model hit its limit; Gemini is the automatic fallback — wait a minute and reopen it from History |
| Want to skip the wait | Open a finished session from **History** — the review is saved |

Keep the dry-run session in History as a backup: if live goes wrong, open it and walk through the review.
