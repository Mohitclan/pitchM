// Captures the REAL PitchMirror pages (served by the local Docker stack) at 1920×1080 @2x.
// The backend's AI is never called: the WebSocket and the sessions API are mocked inside
// the browser with the demo data in demo-data.mjs. Output: assets/captures/*.png + boxes.json
//
//   node capture/capture.mjs [practice|meeting|review|landing|all]
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as demo from './demo-data.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = path.join(ROOT, 'assets', 'captures');
const BASE = process.env.PM_BASE || 'http://localhost:8080';
const only = process.argv[2] || 'all';
fs.mkdirSync(OUT, { recursive: true });

const boxesFile = path.join(OUT, 'boxes.json');
const boxes = fs.existsSync(boxesFile) ? JSON.parse(fs.readFileSync(boxesFile, 'utf8')) : {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const INIT = () => {
  // No always-on-top window in automation: the notch uses its on-page form (same DOM + CSS).
  try { delete window.documentPictureInPicture; } catch (_) { /* ignore */ }
  Object.defineProperty(window, 'documentPictureInPicture', { value: undefined, configurable: true });
  if ('documentPictureInPicture' in window) { try { delete window.documentPictureInPicture; } catch (_) {} }
  // "Choose meeting audio": return a quiet synthetic audio track instead of a picker.
  if (navigator.mediaDevices) {
    navigator.mediaDevices.getDisplayMedia = async () => {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      gain.gain.value = 0;
      window.__pmThemGain = gain;   // the capture script raises this while the client speaks
      osc.connect(gain);
      const dest = ctx.createMediaStreamDestination();
      gain.connect(dest);
      osc.start();
      const canvas = Object.assign(document.createElement('canvas'), { width: 16, height: 16 });
      canvas.getContext('2d').fillRect(0, 0, 16, 16);
      const v = canvas.captureStream(1).getVideoTracks()[0];
      Object.defineProperty(v, 'label', { value: 'Meeting tab' });
      return new MediaStream([v, ...dest.stream.getAudioTracks()]);
    };
  }
};

async function newPage(browser) {
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,
    permissions: ['microphone', 'camera', 'clipboard-read', 'clipboard-write'],
  });
  await context.addInitScript(INIT);
  const page = await context.newPage();
  page.on('pageerror', (e) => console.warn('[pageerror]', e.message));
  // Face tracking needs a real face and GPU; the fake camera has neither. Skip it.
  await page.route('**/static/gaze.js*', (route) => route.fulfill({ contentType: 'application/javascript', body: '/* gaze off for capture */' }));
  // Sessions API → demo sessions only.
  await page.route('**/api/sessions**', async (route) => {
    const url = new URL(route.request().url());
    const m = url.pathname.match(/\/api\/sessions\/([^/]+)$/);
    if (m) {
      const id = decodeURIComponent(m[1]);
      const data = id === 'demo-meeting-01' ? demo.meetingScorecard : demo.practiceScorecard;
      return route.fulfill({ json: data });
    }
    if (url.pathname.endsWith('/stats')) return route.fulfill({ json: { total: 2 } });
    return route.fulfill({ json: { sessions: demo.historyList } });
  });
  return { context, page };
}

/** Mock /ws. Returns a controller: send(msg), onClient(fn). */
async function mockSocket(page) {
  const ctl = { ws: null, handlers: [], ready: null };
  let resolve;
  ctl.ready = new Promise((r) => { resolve = r; });
  await page.routeWebSocket(/\/ws(\?|$)/, (ws) => {
    ctl.ws = ws;
    ws.onMessage((m) => { if (typeof m === 'string') ctl.handlers.forEach((h) => h(JSON.parse(m))); });
    resolve();
  });
  ctl.send = (msg) => ctl.ws.send(JSON.stringify(msg));
  ctl.onClient = (fn) => ctl.handlers.push(fn);
  return ctl;
}

async function settle(page, ms = 450) {
  await page.evaluate(() => document.fonts.ready);
  await sleep(ms);
}

async function shot(page, name, selectors = {}) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), timeout: 20000 });
  const rects = {};
  for (const [key, sel] of Object.entries(selectors)) {
    const r = await page.locator(sel).first().boundingBox({ timeout: 300 }).catch(() => null);
    if (r) rects[key] = { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
  }
  boxes[name] = rects;
  fs.writeFileSync(boxesFile, JSON.stringify(boxes, null, 1));
  console.log('shot', name);
}

async function play(page, ctl, script, onStep) {
  const start = Date.now();   // wall clock, so screenshot time doesn't stretch the script
  for (let i = 0; i < script.length; i++) {
    const [at, msg] = script[i];
    await sleep(Math.max(0, start + at * 1000 - Date.now()));
    ctl.send(msg);
    if (onStep) await onStep(i, msg);
  }
}

/* ── Landing ───────────────────────────────────── */
async function captureLanding(browser) {
  const { context, page } = await newPage(browser);
  await page.goto(`${BASE}/`);
  await settle(page, 800);
  await shot(page, 'landing-hero', { practice: 'text=Practice in the app', meeting: 'text=Coach me in a real meeting' });
  await context.close();
}

/* ── Practice ──────────────────────────────────── */
async function capturePractice(browser) {
  const { context, page } = await newPage(browser);
  const ctl = await mockSocket(page);
  await page.goto(`${BASE}/app`);
  await settle(page, 900);
  const intakeSel = {
    persona: '#coach-persona', mode: '#coach-mode', context: '#delivery-context', goal: '#primary-goal',
    start: '#btn-start', transcript: '#transcript', suggestions: '.suggestions-panel, #suggestions-feed',
  };
  await shot(page, 'app-idle', intakeSel);
  await page.selectOption('#coach-persona', { label: 'Brutal VC' });
  await shot(page, 'app-intake-1', intakeSel);
  await page.selectOption('#coach-mode', { label: 'Pitch' });
  await shot(page, 'app-intake-2', intakeSel);
  await page.selectOption('#primary-goal', { label: 'Improve pacing' });
  await shot(page, 'app-intake-3', intakeSel);

  await page.click('#btn-start');
  await ctl.ready;
  const liveSel = {
    transcript: '#transcript', suggestions: '#suggestions-feed', firstCard: '#suggestions-feed .suggestion-card',
    metrics: '.metrics-bar, .agent-activity', stop: '#btn-stop', video: '.video-wrapper, #video-wrapper',
  };
  let n = 0;
  await play(page, ctl, demo.practiceScript, async (i, msg) => {
    if (['transcript', 'coach_tip', 'coach_said'].includes(msg.type)) {
      await settle(page, 350);
      await shot(page, `practice-live-${String(++n).padStart(2, '0')}`, liveSel);
    }
  });
  await settle(page, 600);
  await shot(page, 'practice-live-final', liveSel);

  // End → analysis pipeline → scorecard (all mocked).
  await page.click('#btn-stop');
  await sleep(300);
  ctl.send({ type: 'status', state: 'analyzing' });
  await settle(page, 500);
  await shot(page, 'practice-analyzing-0', { panel: '#analysis-panel' });
  for (const [k, step] of ['delivery_done', 'content_done', 'research_done', 'synthesis_done'].entries()) {
    ctl.send({ type: 'pipeline_step', step });
    await settle(page, 300);
    await shot(page, `practice-analyzing-${k + 1}`, { panel: '#analysis-panel' });
  }
  ctl.send({ type: 'analysis_complete', report: demo.practiceScorecard.final_report, research_tips: '', generated_assets: [], agents_used: [] });
  ctl.send({ type: 'scorecard', data: demo.practiceScorecard });
  await settle(page, 1500);
  await shot(page, 'practice-score', { score: '.sc-score, .scorecard-score, #scorecard-panel', panel: '#scorecard-panel' });
  await context.close();
}

/* ── Meeting ───────────────────────────────────── */
async function captureMeeting(browser) {
  const { context, page } = await newPage(browser);
  const ctl = await mockSocket(page);
  await page.goto(`${BASE}/meeting`);
  await settle(page, 900);
  const setupSel = {
    mode: '#mt-mode', platform: '#mt-platform', brief: '#mt-brief summary', notes: '#mt-notes', points: '#mt-points',
    minutes: '#mt-minutes', other: '#mt-other-btn', otherStatus: '#mt-other-status', start: '#mt-start', camera: '#mt-camera',
  };
  await page.uncheck('#mt-camera');
  await shot(page, 'meeting-setup-0', setupSel);
  await page.selectOption('#mt-mode', { label: 'Sales call / demo' });
  await page.selectOption('#mt-platform', { label: 'Google Meet' });
  await shot(page, 'meeting-setup-1', setupSel);
  await page.click('#mt-brief summary');
  await settle(page, 300);
  await shot(page, 'meeting-setup-2', setupSel);
  // Type the brief in a few steps so the film can show it being written.
  const notes = demo.meetingBrief.notes;
  for (const frac of [0.35, 0.7, 1]) {
    await page.fill('#mt-notes', notes.slice(0, Math.round(notes.length * frac)));
    await shot(page, `meeting-notes-${frac}`, setupSel);
  }
  const pts = demo.meetingBrief.points;
  for (let i = 1; i <= pts.length; i++) {
    await page.fill('#mt-points', pts.slice(0, i).join('\n'));
    await shot(page, `meeting-points-${i}`, setupSel);
  }
  await page.selectOption('#mt-minutes', { label: '30 min' });
  await shot(page, 'meeting-setup-3', setupSel);
  await page.click('#mt-other-btn');
  await settle(page, 400);
  await shot(page, 'meeting-setup-4', setupSel);

  await page.click('#mt-start');
  await ctl.ready;
  // In real Chrome the notch floats in its own always-on-top window; show that page note.
  await page.evaluate(() => { const n = document.getElementById('mt-pip-note');
    if (n) n.textContent = 'The coach bubble is floating on top of your screen. Drag it anywhere — it stays above Zoom, Meet and Teams.'; });
  ctl.onClient((m) => {
    if (m.type === 'help' && demo.meetingHelpReplies[m.kind]) setTimeout(() => ctl.send(demo.meetingHelpReplies[m.kind]), 700);
  });
  const liveSel = { bubble: '.pm-bubble, #pm-bubble, .pm-notch', pill: '.pm-pill', drop: '.pm-drop', hint: '.pm-hint',
    help: '.pm-help', ask: '[data-help="ask"]', catchup: '[data-help="catchup"]', answer: '[data-help="answer"]',
    end: '.pm-end, .pm-stop', transcript: '#mt-transcript', nudges: '#mt-nudges' };
  let n = 0;
  await settle(page, 600);
  await shot(page, 'meeting-live-00', liveSel);
  // Real setting: turn off "Dim when idle" so every frame is at full opacity.
  await page.evaluate(() => { const c = document.querySelector('.pm-opt-dim'); if (c && c.checked) c.click(); });
  const themAudio = (on) => page.evaluate((v) => { if (window.__pmThemGain) window.__pmThemGain.gain.value = v; }, on ? 0.08 : 0);
  await play(page, ctl, demo.meetingScript, async (i, msg) => {
    if (msg.speaker === 'other') await themAudio(msg.type === 'transcript');
    await settle(page, 350);
    await shot(page, `meeting-live-${String(++n).padStart(2, '0')}`, liveSel);
  });
  await settle(page, 1200);
  await shot(page, 'meeting-live-rest', liveSel);
  await sleep(9000);   // let the tip fold back to the resting pill
  await shot(page, 'meeting-live-resting', liveSel);
  // Details (⌄): mood chip, meters, pace, talk share.
  await page.locator('.pm-details-btn').first().dispatchEvent('click');
  await settle(page, 600);
  await shot(page, 'meeting-details', liveSel);
  await page.locator('.pm-details-btn').first().dispatchEvent('click');
  await settle(page, 400);
  // Help button "? Ask" — the real handler sends {type:'help'}; the mock replies.
  await page.locator('.pm-pill').first().dispatchEvent('click');
  await settle(page, 400);
  await shot(page, 'meeting-peek', liveSel);
  await page.locator('[data-help="ask"]').first().dispatchEvent('click');
  await settle(page, 1300);
  await shot(page, 'meeting-help-ask', liveSel);
  // End → mocked scorecard → "review ready" in the notch.
  await page.locator('.pm-end').first().dispatchEvent('click');
  await settle(page, 500);
  await shot(page, 'meeting-ending', liveSel);
  ctl.send({ type: 'status', state: 'analyzing' });
  await sleep(500);
  ctl.send({ type: 'scorecard', data: demo.meetingScorecard });
  await settle(page, 900);
  await shot(page, 'meeting-saved', { ...liveSel, result: '#mt-result' });
  await context.close();
}

/* ── Review (opened from History, like a user would) ─ */
async function captureReview(browser) {
  const { context, page } = await newPage(browser);
  await page.goto(`${BASE}/app?session=demo-meeting-01`);
  await settle(page, 1500);
  const sel = { panel: '#scorecard-panel', recap: '.recap-section', email: '.rc-email', copy: '.rc-email-actions button',
    qa: '.qa-section', qa1: '.qa-card', review: '.review-split', download: '.review-download' };
  await shot(page, 'review-meeting-top', sel);
  const panel = page.locator('.right-panel');
  for (const [name, target] of [['review-meeting-recap', '.recap-section'], ['review-meeting-email', '.rc-email'],
    ['review-meeting-qa', '.qa-section'], ['review-meeting-transcript', '.review-split']]) {
    const el = page.locator(target).first();
    if (!(await el.count())) { console.warn('missing', target); continue; }
    await el.evaluate((node) => {
      const sc = node.closest('.right-panel') || document.scrollingElement;
      sc.scrollTop += node.getBoundingClientRect().top - 90;
    });
    await settle(page, 500);
    await shot(page, name, sel);
  }
  // Copy the follow-up email (real button; clipboard permission is granted to the page).
  const copyBtn = page.locator('.rc-email-actions button').first();
  await copyBtn.evaluate((node) => { const sc = node.closest('.right-panel'); sc.scrollTop += node.closest('.rc-email').getBoundingClientRect().top - 90; });
  await settle(page, 300);
  await copyBtn.click();
  await settle(page, 500);
  await shot(page, 'review-meeting-email-copied', sel);
  if (await panel.count()) await panel.evaluate((p) => { p.scrollTop = 0; });
  await page.goto(`${BASE}/app?view=history`);
  await settle(page, 1200);
  await shot(page, 'history', { list: '#history-list', first: '.history-row' });
  await context.close();
}

const browser = await chromium.launch({
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    `--use-file-for-fake-video-capture=${path.join(ROOT, 'assets', 'media', 'camera-off.y4m')}`, '--autoplay-policy=no-user-gesture-required'],
});
try {
  if (only === 'all' || only === 'landing') await captureLanding(browser);
  if (only === 'all' || only === 'practice') await capturePractice(browser);
  if (only === 'all' || only === 'meeting') await captureMeeting(browser);
  if (only === 'all' || only === 'review') await captureReview(browser);
} finally {
  await browser.close();
}
