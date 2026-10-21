# PitchMirror product films

Two product films, made only with free and local tools:

| File | Length | Purpose |
|------|--------|---------|
| `out/product-overview-60s.mp4` | 60 s | What PitchMirror is and why it matters |
| `out/product-walkthrough-90s.mp4` | 90 s | How it works, step by step |

Both are 1920×1080, 30 fps, H.264, with AAC stereo at 48 kHz. The audio is mixed to −16 LUFS.

**Docs:**
- [RESEARCH.md](RESEARCH.md): references and approach
- [storyboard.md](storyboard.md): the visual design
- [script-60s.md](script-60s.md) and [script-90s.md](script-90s.md): the narration
- [ASSET_MANIFEST.md](ASSET_MANIFEST.md): what each asset is and what was simulated

## How it's made

```
capture/   Playwright drives the REAL app (localhost:8080) with mocked socket/API + demo data → assets/captures/*.png
tts/       Kokoro narration, music synthesis and the audio mix (run in a local Docker image)
engine/    stage.html + stage.js render a composition at any time t; comps.js holds both films as data;
           render.mjs steps frames with Playwright, encodes with FFmpeg, mixes and muxes audio
out/       rendered films (+ work/ scratch, stills/ review frames)
```

No AI service is called at any point. The narration comes from Kokoro running on this machine, and the music is synthesised locally.

## Requirements
- Node 22, FFmpeg and Docker Desktop.
- `npm install` in this folder, then `npx playwright install chromium`.
- For new captures, the PitchMirror stack must be running: `docker compose up -d` in the repo root.

## Rebuild from scratch

Build the narration image once (about 3.5 GB; downloads Kokoro and the voices):

```bash
docker build -t pitchmirror-kokoro tts
```

Regenerate the narration from `tts/lines.json`:

```bash
docker run --rm -v "$PWD":/work pitchmirror-kokoro python tts/speak.py tts/lines.json assets/audio/vo
```

Regenerate the music beds:

```bash
docker run --rm -v "$PWD":/work pitchmirror-kokoro python tts/music.py 60 assets/audio/music-60.wav
```

```bash
docker run --rm -v "$PWD":/work pitchmirror-kokoro python tts/music.py 90 assets/audio/music-90.wav
```

Re-capture the app screens (the stack must be running):

```bash
node capture/capture.mjs all
```

Render both films into `out/`:

```bash
node engine/render.mjs ProductOverview60
```

```bash
node engine/render.mjs ProductWalkthrough90
```

## Preview and review

Stills at chosen seconds go to `out/stills/<comp>/`:

```bash
node engine/render.mjs ProductWalkthrough90 --stills 10,30,55
```

A short section with audio goes to `out/*-preview-*.mp4`:

```bash
node engine/render.mjs ProductOverview60 --from 14 --to 24
```

To scrub in a browser, serve this folder and open `engine/stage.html?comp=ProductOverview60`, then call `renderAt(seconds)` in the console.

## Updating the films

| To change… | Edit |
|------------|------|
| Narration wording | `tts/lines.json`, regenerate that line (`… speak.py tts/lines.json assets/audio/vo b6`), then check its new length in `assets/audio/vo/timing.json` |
| Timing, camera moves, cursor, labels, which screenshot shows when | `engine/comps.js`. All coordinates are in the 1920×1080 capture space; element boxes are in `assets/captures/boxes.json` |
| CTA or tagline | `CTA` / `TAGLINE` at the top of `engine/comps.js` |
| The demo story shown in the UI | `capture/demo-data.mjs`, then `node capture/capture.mjs all` |
| Colours, fonts, frame, label style | `engine/stage.html` |
| Music level or ducking | `music_gain_db` / `duck_db` in `engine/render.mjs` |

## Checking output

Show codec, size, frame rate, audio and duration:

```bash
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,channels,sample_rate:format=duration -of compact out/product-overview-60s.mp4
```

Measure loudness:

```bash
ffmpeg -hide_banner -i out/product-overview-60s.mp4 -af ebur128=peak=true -f null - 2>&1 | tail -12
```

## Licences
- **Playwright:** Apache-2.0.
- **FFmpeg:** LGPL/GPL.
- **Kokoro-82M:** Apache-2.0.
- **Music:** original, synthesised in `tts/music.py`.
- **Fonts:** Bricolage Grotesque and Manrope, both under the SIL Open Font License, loaded from Google Fonts.
- **No Remotion.** It needs a paid licence for companies with more than 3 people.
- **No Apple material.** Apple films were studied for principles only; see RESEARCH.md.
