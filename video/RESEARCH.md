# PitchMirror product films — research

Research date: 2026-10-20. This file records what was actually reviewed and how it was accessed. No Apple footage, logos, music or ad copy is reused. Only general principles are taken.

## 1. Apple references

### How they were reviewed
Films were opened on Apple's official YouTube channel in a browser. Frames were sampled by seeking every 3–10 s, with the audio muted.

- **Seen:** layout, typography, backgrounds and scene order, from the sampled frames.
- **Not heard:** music and voice. YouTube's auto-captions showed only cues like "Music playing", plus some lines of dialogue.
- **Not seen frame by frame:** motion between the sampled frames.

| # | Reference | URL | Access |
|---|-----------|-----|--------|
| A | *Safari Helps Keep Distractions Hidden* (15 s software feature film) | https://www.youtube.com/watch?v=iUr53gDq380 | 5 sampled frames + captions |
| B | *The new Apple HomeView* (64 s, UI-heavy product film) | https://www.youtube.com/watch?v=QeQAXuQpGEA | 8 sampled frames + captions |
| C | *iPhone 18 Pro* (55 s product film) | https://www.youtube.com/watch?v=DDnNc60onT4 | 6 sampled frames |
| D | Apple Intelligence product page | https://www.apple.com/apple-intelligence/ | page text/markup only |
| E | MacBook Pro product page | https://www.apple.com/macbook-pro/ | page text/markup only (film link present, not played) |
| F | iPhone product page | https://www.apple.com/iphone/ | page text only |
| — | Apple Newsroom | https://www.apple.com/newsroom/ | navigation only — nothing usable |

### Observations

**A. Safari, distractions (closest to a software feature demo)**
- **Opening:** one person stands on a seamless soft-blue studio sweep. Nothing else is in frame.
- **Problem:** cluttered web pop-ups crowd in around the person, which personifies the frustration.
- **Fix:** one gesture resolves it, and the clutter disappears.
- **Ending:** one white sentence-case line centred on the same blue backdrop, then the product mark. About 15 s, with one idea and no feature list.

**B. HomeView (UI-heavy)**
- **Opening:** the product name in black on a plain white field.
- **Features:**
  - a fast montage of the real UI (a grid of widgets)
  - a real spoken request shown as a large headline above the device
  - a full-frame white card with one black sentence (a feature line, about 5 words)
  - the device on a soft grey sweep with the real UI on its screen
- **Close-ups:** they push in on the device but keep its edges visible, so the context is never lost.
- **Ending:** it fades into a dark frame.

**C. iPhone 18 Pro**
- **Opening:** a black void and a single beam of light revealing the product.
- **Stats:** headlines sit centred in white, with a smaller grey qualifier line underneath.
- **Background:** near-black throughout, with one accent of light.
- **Ending:** a large product wordmark.
- **Music:** a licensed song credited in the captions. We will not do that.

**D–F. Product pages**
- Each section makes one claim, with a short headline of 2–5 words that ends in a period.
- A big stat or a single UI moment acts as the proof.
- Paired start/end frame assets suggest scroll-scrubbed before→after transitions.
- A dedicated privacy beat appears near the end.

**Not observed directly:** exact typefaces, camera easing curves, cut timing between samples, and sound design. Where the plan below relies on these, it uses widely known conventions rather than claims about these films.

### Principles we will use
1. **One idea per scene:** one claim plus one UI proof. Never two features on screen at once.
2. **Line first, proof second:** a short sentence-case headline appears on a plain field, then the real UI answers it.
3. **Human problem before product:** open on the feeling ("you rush, you say um") before showing any interface.
4. **Restrained fields:** off-white `#F5F5F7`-style backgrounds for the app, and near-black for the Meeting Mode notch, which is dark in the real product. One accent colour.
5. **Push in, never lose the frame:** zoom from 1.0 to about 1.6× toward the component that matters, keeping enough surrounding UI for context.
6. **Real UI on screen:** captured screenshots and recordings inside a clean browser frame. Nothing re-drawn or generated.
7. **Stat-style proof lines:** a white or black line with a grey qualifier, such as "184 wpm · 20-second window", using only numbers that the captured session actually produced.
8. **Calm transitions:** crossfades and short push/match cuts. No spins, glitches, particles or glow.
9. **Trust beat:** nudges are text only, so nobody in the call sees or hears them. This is verified in the product.
10. **Quiet ending:** product name, one line, CTA, then a slow fade to the background colour.
11. **Sound:** a soft ambient bed that ducks under the voice. Clicks only where a real click happens.

## 2. Implementation templates reviewed

All three repos were accessible and are MIT-licensed. They were reviewed through the GitHub API and raw README files, not cloned.

| Repo | What it is | Useful parts | Gaps |
|------|-----------|--------------|------|
| [noamdorr/saas-product-demo-video](https://github.com/noamdorr/saas-product-demo-video) | A Claude Code *skill* (docs + TSX snippets), not a runnable project | cursor hotspot math, click-positioning playbook, beat-sync script | no VO, captions or browser frame; assumes mocked UI |
| [codeverbojan/remotion-cinematic](https://github.com/codeverbojan/remotion-cinematic) | Full Remotion 4.0.451 engine, 1920×1080@30, ~500 tests | `Cursor`, `CameraRig`/`AutoZoom`, `AudioManager` (music + SFX ducking), `ScenePush`, Zod props | no captions/VO; built around synthetic UI descriptors |
| [rickthomasjr/remotion-product-video](https://github.com/rickthomasjr/remotion-product-video) | Small Remotion starter (new, 0 stars) | `BrowserFrame`, `HeroShot` (Ken Burns), `Annotation`, VO-aligned `Captions`, VO + music wiring | no cursor, zoom or transitions |
| [Remotion docs](https://www.remotion.dev/docs/) | Framework | `Composition`, `Sequence`, `interpolate`/`spring`, `<Audio volume={f=>…}>`, `@remotion/transitions`, `npx remotion render` | — |

**Remotion licensing (important).** Remotion is free for individuals and for companies with **3 or fewer employees**. A for-profit company with more than 3 employees needs a paid Company License to use it commercially. See `LICENSE.md` in remotion-dev/remotion; check current pricing at remotion.dev/license. A product video for PanScience is probably commercial use.

### Selected approach (final): free tools only
Remotion was **not** used. A company of more than 3 people needs a paid Remotion licence, and the brief was to keep everything free. The template ideas were reimplemented in a small engine built only on free tools (Playwright under Apache-2.0, FFmpeg under LGPL/GPL).

**Where the ideas came from**
- **remotion-cinematic:** camera rig (keyframed zoom and focus), cursor with arcs and click feedback, and music ducking under speech.
- **remotion-product-video:** browser frame, feature labels, and narration-driven timing.
- **saas-product-demo-video:** click-target math, with cursor points taken from real element boxes (`boxes.json`).

**How it works**
- `engine/stage.html` and `stage.js` render any moment of a composition as a pure function of time.
- `engine/render.mjs` steps it frame by frame with Playwright and encodes H.264 with FFmpeg.
- The two compositions, `ProductOverview60` and `ProductWalkthrough90`, are plain data in `engine/comps.js`.

## 3. Product understanding (verified in code and the running app)

**PitchMirror** is an AI speaking coach. It listens while you speak, gives short measured nudges, and writes a full review afterwards.

- **Audience:** people who pitch, interview, sell or present, especially on video calls. The pilot is for PanScience colleagues.
- **Problem:** delivery habits (fillers, rushing, losing the camera, rambling, missing key points) are invisible while you speak, and feedback usually comes too late or never.

**Verified features**

*Practice app (`/app`)*
- Session intake: coach persona (Professional Coach / Brutal VC / Supportive Mentor), what you're preparing for, setting (virtual / in-person / hybrid) and primary goal.
- During the session:
  - live transcript on the left, Coach Suggestions on the right
  - measured flags for fillers, pace (WPM in a 20 s window) and eye contact (MediaPipe gaze)
  - spoken cues
  - optional slide PDF
- After the session: an agent pipeline (Delivery, Content, Research, Synthesis, Memory) feeds a Session Scorecard with transcript, coaching moments, Q&A review and a .txt download.

*Meeting Mode (`/meeting`)*
- Setup:
  - meeting type and platform (Zoom / Google Meet / Microsoft Teams / Webex / Other)
  - Pre-call brief with context notes, points to cover and planned length
  - **Choose meeting audio**, so the other person is heard too (tab audio)
- **The notch:** a dark pill in an always-on-top window that shows status, the latest tip, a points ring and a timer.
  - Drop-down tips, and an 💡 Answer outline when you're asked a question.
  - Help buttons: Answer / Catch up / Ask.
  - Client read (mood) chip, You/Them meters, 👍/👎 on tips, and settings, pause and End.
- After the meeting: recap with decisions, action items and a **ready-to-send follow-up email**, a question-by-question review with stronger sample answers, and the full transcript.

*Pilot tooling:* tester picker, `/guide`, `/pilot` feedback summary.

**Do not show, or show with care**
- The landing page's illustrative mock panels contain sample numbers ("forty percent faster close rates", the 82 score). These are not real outputs, so the films will not use them as proof.
- Real tester names and sessions in History.
- `/pilot` comments.
- API keys (`.env`).

**Strongest real-world use case:** you're on a client or investor call in Google Meet. The notch nudges you silently, hands you an answer outline when the client asks a hard question, and afterwards drafts the follow-up email.

## 4. Capture approach: real UI, demo data, no API keys
- **Browser:** Playwright with Chromium at 1920×1080 and device scale 2, so zooms stay sharp. A fresh browser profile means no real tester data appears.
- **Data:**
  - The real pages are driven by **mocked** inputs: Playwright's `routeWebSocket` replaces `/ws`, and `route` replaces `/api/sessions`.
  - Fictional demo data from `capture/demo-data.mjs` is played through them.
  - **No Gemini or Claude call is made**, and nothing is written to the database.
  - Every pixel is still drawn by the product's own code.
- **Meeting Mode:**
  - The notch is captured in its on-page form, which uses the same DOM and `bubble.css` as the always-on-top window.
  - The "meeting tab audio" is a synthetic tone that plays only while the client speaks, so the real "hold tips while the client talks" logic behaves correctly.
- **Camera:** Chrome's fake camera is fed a plain dark frame instead of its green test pattern.
- **Cost:** nothing beyond local CPU time.
