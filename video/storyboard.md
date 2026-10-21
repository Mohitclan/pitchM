# Storyboard — PitchMirror films

The exact frame data is in `engine/comps.js`. This page explains the design behind it.

## Visual language (shared by both films)

**Canvas:** 1920×1080 at 30 fps, H.264, AAC stereo at 48 kHz.

**Backgrounds:** off-white `#F5F6F8` throughout. The product UI is light, and the dark notch reads strongly against it.

**Type:** the product's own fonts.
- Headlines: Bricolage Grotesque 700, sentence case, tight tracking.
- Labels: Manrope.

**Colour:** product blue `#1D4ED8` is the only accent (wordmark ring, cursor click ripple, highlight ring).

**Browser frame:** 1640 px wide, minimal light chrome, URL pill "pitchmirror · localhost", 18 px radius, soft shadow. Captures are 2× (3840×2160), so push-ins up to 2.2× stay sharp.

**Camera:**
- One eased move per beat, using a cubic in/out curve.
- Focus points are given in the captured page's coordinates.
- The camera is clamped so a zoom never pulls the frame edge into view.

**Cursor:**
- Arrow with a slight arc between points, a 120 ms press and a blue ripple.
- It moves only to elements the real capture clicked: selects, Start Session, Pre-call brief, Choose meeting audio, Start meeting coach, the notch pill, ? Ask, Copy.
- A soft click sound plays on each click.

**Labels:** a white pill, bottom-left or bottom-right, with a headline in Bricolage and a subline in Manrope. Fades in and out over about 0.4 s.

**Transitions:**
- 0.5 s crossfades between scenes.
- 0.22 s crossfades between captured UI states, so the UI appears to change live.
- 0.6 s crossfades for scroll changes.

**Captions:** none burned in. The narration is short and the labels carry the key words. Word timestamps are saved in `assets/audio/vo/timing.json` if a captioned version is needed.

## ProductOverview60

| # | Time | Scene | Real assets |
|---|------|-------|-------------|
| 1 | 0–5.3 | Headline: You know what to say. | — |
| 2 | 4.8–12.3 | Problem words, timed to the voice | — |
| 3 | 11.8–15.6 | Wordmark reveal + whoosh | — |
| 4 | 15.1–31 | Practice: Start Session, then live transcript, Filler card, Pace card; push to suggestions | `app-intake-3`, `practice-live-01…10` |
| 5 | 30.5–43.3 | Meeting Mode: push into the notch through resting, outline, nudge and heads-up states | `meeting-live-02/03/05/13/14` |
| 6 | 42.8–54.6 | Scorecard, then Q&A review, then follow-up email | `practice-score`, `review-meeting-qa`, `review-meeting-email` |
| 7 | 54.1–60 | End card + CTA, fade to off-white | — |

## ProductWalkthrough90

| # | Time | Scene | Real assets |
|---|------|-------|-------------|
| 1 | 0–6.3 | Wordmark: Coached while you speak. | — |
| 2 | 5.8–15.4 | "Measured live", with Fillers · Pace · Eye contact chips | — |
| 3 | 14.9–25.6 | Session Intake clicks, then Start Session | `app-idle`, `app-intake-1…3`, `practice-live-01` |
| 4 | 25.1–38.6 | Live transcript, then Coach Suggestions | `practice-live-02…10` |
| 5 | 38.1–50.4 | Meeting setup: brief, points, 30 min, Choose meeting audio, Start | `meeting-setup-*`, `meeting-notes-*`, `meeting-points-*`, `meeting-live-01` |
| 6 | 49.9–64.4 | The notch: outline, points, nudges, then **? Ask** help | `meeting-live-*`, `meeting-peek`, `meeting-help-ask` |
| 7 | 63.9–78.1 | Recap, then follow-up email, then **Copy**, then Copied ✓ | `review-meeting-top/recap/email/email-copied` |
| 8 | 77.6–86.6 | Q&A review, then transcript with **Download .txt** | `review-meeting-qa`, `review-meeting-transcript` |
| 9 | 86.1–90 | End card + CTA | — |

## Audio
- **Narration:** Kokoro `af_heart`, one line per beat. Each line is normalised to −16 LUFS before mixing.
- **Music:** an original ambient bed synthesised locally by `tts/music.py`:
  - D-major pads with a slow chord loop, sparse plucks after 6 s, and a low root from the first third
  - convolution reverb
  - 2.5 s fade-in, 4 s fade-out
- **Mix (`tts/mix.py`):**
  - music at −9 dB, ducked a further 9 dB under speech (80 ms attack, 600 ms release)
  - clicks on real clicks, one whoosh on the 60 s reveal
- **Master:** two-pass `loudnorm` to −16 LUFS integrated, true peak −1.5 dBTP.
