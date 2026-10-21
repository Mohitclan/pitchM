# Meeting Mode: solo end-to-end test (about 25 minutes)

You play the presenter. A dummy client, **Priya Sharma (Northwind Dental)**, is a voiced web page that "asks" the
questions. Everything the coach should do is listed in `client.html` next to each question.

> **Test data warning:** Billing Hub and Clinic Suite are made-up. They go into your *real* Company hub. When you
> finish, open `/company` and press **Remove business** on both so they don't answer real clients.

## 0. Before you start
- Chrome, **headphones on** (the client's voice comes out of a tab; without headphones the mic hears it twice).
- Docker app running (`docker compose up -d`), you're signed in as the admin (`mohit`).
- Open **testkit/client.html** in Chrome (double-click it). Leave that tab open and do not mute it.

## 1. Load the test data (pick one)
**A. One command (fast)**
```bash
cd /Users/admin/Desktop/hub/GeminiLiveAgentHack && docker compose cp testkit pitchmirror:/tmp/testkit && docker compose exec pitchmirror python -m backend.seed_testkit /tmp/testkit mohit
```
This creates both businesses with summaries and documents, your "Northwind client notes" document and the saved
script "Northwind call". Safe to run twice.

**B. By hand (tests the upload screens too)**
1. `/company` → Add a business: name `Billing Hub`, paste `docs/01-…summary.txt` as the summary, attach
   `docs/02-…pricing-and-faq.txt` and `docs/07-…deck.pdf`.
2. `/company` → Add a business: `Clinic Suite`, summary = `docs/03-…summary.txt`, attach that same file.
3. `/prepare` → My documents → upload `docs/04-my-documents-northwind-client-notes.txt`.
4. `/prepare` → **Write my own script instead** → name `Northwind call`, paste `docs/06-my-script-to-save.txt`
   into the Script tab; Key points tab, one per line:
   ```
   Pricing: 49 dollars per clinic per month
   Works with Zoho Books
   Setup takes about two weeks
   Free 30 day pilot on three clinics
   Agree a follow up call next Wednesday
   ```
   Objections tab:
   ```
   Price: Annual plans get two months free, and Brightsmile cut billing time by 40 percent.
   Competitor: We sync to Zoho Books every night, so there is nothing to replace.
   Timing: Setup takes two weeks and runs in parallel for one week.
   Authority: I can send a one page summary for your CFO, Rahul.
   ```
   Save script.
5. Optional AI test (spends a little Claude credit): in `/prepare` paste `docs/05-prepare-me-material.txt` into
   "Your material" → Draft my script. Check it only uses facts from the material and marks gaps with `[add: …]`.

## 2. Meeting Mode setup (`/meeting`)
| Field | Choose |
|---|---|
| Meeting type | Sales demo |
| Coach style | Professional |
| How chatty? | **Active** (shows every kind of advice) |
| Platform | Google Meet (any) |
| Camera | optional |
| Who is it with? | `Northwind Dental` |
| Which company business? | **Billing Hub** (not Clinic Suite, on purpose) |
| Script | **Northwind call** (this fills points, objections and length) |
| Answer from my documents | ✔ ticked |
| Context notes | paste `docs/04-my-documents-northwind-client-notes.txt` |
| Planned length | 15 min |

Press **Start**. In the picker choose **Choose meeting audio** → the **client.html tab** → tick **Share tab audio**.
Open the Answers panel (▤) and the **Script** tab.

## 3. Run the call
Follow `client.html` from step 1 to 11. Each step says what the client says, what you do and what must appear.
Features covered: script follow-along, I'm lost, skipped section, answers from your documents, named-business
fallback (Clinic Suite), "Not in your notes" (SOC 2), objection card, Answer / Catch up / Ask, client mood,
filler nudge, talk-time nudge, commitments, brief points, next-step guard.

## 4. After pressing Stop
Check the scorecard: recap, follow-up email (Copy / Open in mail), question review with stronger answers, transcript,
the **Readiness** card (expect *Almost* or *Not yet* with Proof listed as skipped and fillers flagged), script coverage
per section. Then open History: the call is saved under your account only.

## 5. Pass / fail checklist
- [ ] Priya's voice is transcribed as "Them" (Answers panel Questions tab lists her questions)
- [ ] Script lines fade as you read; next line highlighted; I'm lost points to the right line
- [ ] Pricing answer cites the company hub and says 49 dollars, no setup fee
- [ ] Clinic Suite answer cites Clinic Suite documents (199 dollars, one-click sync)
- [ ] SOC 2 answer says it is not in the notes / hub and invents nothing
- [ ] Objection card shows your prepared Price / Competitor answer first
- [ ] Filler nudge and talk-time nudge appeared
- [ ] Commitments: proposal by Friday, confirm SOC 2
- [ ] Recap, email and Readiness card appear and match what happened
- [ ] A second account (private window) cannot see your call, script or notes, but can read the company hub
- [ ] Cleanup: remove Billing Hub and Clinic Suite from `/company`

Regenerate the client voice or change the lines: edit `testkit/build.py`, run `python3 build.py`.
