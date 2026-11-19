#!/usr/bin/env node
/**
 * End-to-end check of the Pitchlight Chrome extension (extension/) — macOS, headless Chromium.
 *
 * A fake meeting tab plays three of the practice-meeting client's questions; the extension
 * captures that tab; the /meeting page receives the audio and the server must transcribe it as
 * the other person. Then "End meeting" must make the extension stop listening.
 *
 * 1. Start a throwaway server (sign-in off, empty DB, no Claude key → Gemini fallback, free):
 *      AUTH_MODE=off API_BEARER_TOKEN= ANTHROPIC_API_KEY= CORS_ALLOWED_ORIGINS='*' \
 *        LOCAL_DB_PATH=/tmp/pl-ext-test.db .venv/bin/uvicorn backend.main:app --port 8091
 * 2. node scripts/extension_e2e.js            (LISTEN_S=40 to listen longer)
 *
 * Uses Playwright from video/node_modules. Test-only Chrome switches:
 *   --allowlisted-extension-id   lets tab capture start without a real toolbar click
 *   --disable-audio-output       keeps the run silent (Playwright's default --mute-audio would
 *                                silence the captured tab too, so it is turned off)
 */
'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const { chromium } = require(path.join(ROOT, 'video/node_modules/playwright'));
const EXT = path.join(ROOT, 'extension');
const SERVER = process.env.PL_TEST_SERVER || 'http://localhost:8091';
const MEET_PORT = 8092;
const LISTEN_MS = Number(process.env.LISTEN_S || 35) * 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Fake meeting: Chromium can't decode AAC (.m4a), so convert the demo clips to WAV. */
function fakeMeeting(dir) {
  const clips = ['c1', 'c2', 'c3'];
  for (const c of clips) {
    execFileSync('afconvert', ['-f', 'WAVE', '-d', 'LEI16@24000', path.join(ROOT, `frontend/demo/audio/${c}.m4a`), path.join(dir, `${c}.wav`)]);
  }
  fs.writeFileSync(path.join(dir, 'index.html'), `<!doctype html><meta charset="utf-8"><title>Fake meeting</title>
<audio id="a" autoplay></audio><script>
const clips = ${JSON.stringify(clips.map((c) => `${c}.wav`))}; let i = 0; const a = document.getElementById('a');
function next() { a.src = clips[i++ % clips.length]; a.play().catch(() => {}); }
a.addEventListener('ended', () => setTimeout(next, 1500)); next();
</script>`);
  const server = http.createServer((req, res) => {
    const file = path.join(dir, path.basename(new URL(req.url, 'http://x').pathname) || 'index.html');
    if (!fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': file.endsWith('.wav') ? 'audio/wav' : 'text/html' });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(MEET_PORT, '127.0.0.1', () => resolve(server)));
}

function launch(profile, extra = []) {
  return chromium.launchPersistentContext(profile, {
    channel: 'chromium',
    headless: true,
    ignoreDefaultArgs: ['--mute-audio'],
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`,
      '--autoplay-policy=no-user-gesture-required', '--disable-audio-output', ...extra],
  });
}

async function extensionWorker(ctx) {
  const isExt = (w) => /^chrome-extension:\/\/[a-p]{32}\/background\.js$/.test(w.url());
  const w = ctx.serviceWorkers().find(isExt) || await ctx.waitForEvent('serviceworker', { predicate: isExt, timeout: 10000 });
  for (let i = 0; i < 50; i++) {
    if (await w.evaluate(() => !!(globalThis.chrome?.storage && globalThis.pitchlight)).catch(() => false)) return w;
    await sleep(200);
  }
  throw new Error('extension service worker never became ready');
}

(async () => {
  try {
    const res = await fetch(`${SERVER}/health`);
    if (!res.ok) throw new Error(String(res.status));
  } catch (_) {
    console.error(`No Pitchlight test server at ${SERVER}. Start it first (see the top of this file).`);
    process.exit(2);
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pl-ext-e2e-'));
  const meetServer = await fakeMeeting(tmp);
  const profile = path.join(tmp, 'profile');
  const checks = [];
  const check = (name, ok, detail = '') => { checks.push({ name, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`); };

  // Learn the unpacked extension id, then relaunch with tab capture allowed for it.
  let ctx = await launch(profile);
  const extId = new URL((await extensionWorker(ctx)).url()).host;
  await ctx.close();
  ctx = await launch(profile, [`--allowlisted-extension-id=${extId}`]);
  try {
    const sw = await extensionWorker(ctx);
    await sw.evaluate((s) => chrome.storage.sync.set({ serverUrl: s }), SERVER);
    // Headless has no microphone: give the Pitchlight page a near-silent synthetic one.
    await ctx.addInitScript((port) => {
      if (location.port !== port) return;
      const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async (c) => {
        if (c && c.audio && !c.audio.mandatory) {
          const ac = new AudioContext(); const d = ac.createMediaStreamDestination();
          const o = ac.createOscillator(); const g = ac.createGain(); g.gain.value = 0.001;
          o.connect(g); g.connect(d); o.start();
          return d.stream;
        }
        return real(c);
      };
    }, new URL(SERVER).port);

    const meet = await ctx.newPage();
    await meet.goto(`http://127.0.0.1:${MEET_PORT}/index.html`);
    await sleep(1500);
    await meet.bringToFront();

    const pageOpened = ctx.waitForEvent('page', { timeout: 15000 });
    const capture = await sw.evaluate(async (port) => {
      const [tab] = await chrome.tabs.query({ url: `http://127.0.0.1:${port}/*` });
      try { return await globalThis.pitchlight.startCapture(tab, null); } catch (e) { return { error: String(e.message || e) }; }
    }, MEET_PORT);
    check('extension captures the meeting tab', !capture.error, capture.error || `playback ${capture.playback}`);
    check('meeting stays audible to the user', capture.playback === 'running');
    if (capture.error) throw new Error('capture failed');

    const pl = await pageOpened;
    check('Pitchlight meeting page opens', pl.url().startsWith(`${SERVER}/meeting`), pl.url());
    let sent06 = 0; let voiced = 0; const them = []; const types = {};
    pl.on('websocket', (ws) => {
      ws.on('framesent', (f) => {
        if (typeof f.payload === 'string' || f.payload[0] !== 0x06) return;
        sent06++;
        const pcm = f.payload.subarray(1); let sq = 0;
        for (let i = 0; i + 1 < pcm.length; i += 2) { const v = pcm.readInt16LE(i) / 32768; sq += v * v; }
        if (Math.sqrt(sq / (pcm.length / 2)) > 0.012) voiced++;
      });
      ws.on('framereceived', (f) => {
        if (typeof f.payload !== 'string') return;
        try {
          const m = JSON.parse(f.payload);
          types[m.type] = (types[m.type] || 0) + 1;
          if (m.type === 'transcript' && m.speaker === 'other') them.push(m.text);
        } catch (_) { /* not JSON */ }
      });
    });
    await pl.waitForLoadState('domcontentloaded');
    const connected = await pl.waitForFunction(
      () => /Pitchlight extension/.test(document.getElementById('mt-other-status')?.textContent || ''), null, { timeout: 15000 },
    ).then(() => true, () => false);
    check('page shows the extension connection', connected);

    await pl.uncheck('#mt-camera');
    await pl.click('#mt-start');
    await sleep(LISTEN_MS);
    check('meeting audio frames reach the server', sent06 > LISTEN_MS / 200, `${sent06} frames`);
    check('those frames carry speech, not silence', voiced > 20, `${voiced} voiced chunks`);
    check('server transcribes the other person', them.length > 0, them[0] ? `"${them[0]}"` : 'none');
    console.log('      server messages:', JSON.stringify(types));
    check('badge shows ON while listening', (await sw.evaluate(() => chrome.action.getBadgeText({}))) === 'ON');

    await pl.click('#mt-end');
    await sleep(4000);
    check('ending the meeting stops the extension', (await sw.evaluate(() => globalThis.pitchlight.getCapture())) === null);
    check('capture document is closed', (await sw.evaluate(async () => (await chrome.runtime.getContexts({ contextTypes: ['OFFSCREEN_DOCUMENT'] })).length)) === 0);
  } catch (err) {
    check('run completed', false, String(err.message || err));
  } finally {
    await ctx.close();
    meetServer.close();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  const failed = checks.filter((c) => !c.ok).length;
  console.log(failed ? `\n${failed} check(s) failed.` : '\nAll checks passed.');
  process.exit(failed ? 1 : 0);
})();
