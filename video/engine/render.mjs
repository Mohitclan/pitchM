// Render a composition to MP4: Playwright steps engine/stage.html frame by frame, FFmpeg
// encodes H.264, then the audio (Kokoro VO + local music + click sounds) is mixed with
// ducking, loudness-normalised and muxed.
//
//   node engine/render.mjs ProductOverview60                 full render → out/
//   node engine/render.mjs ProductOverview60 --from 14 --to 22   a short section
//   node engine/render.mjs ProductOverview60 --stills 3,17,33    PNG stills for review
//   node engine/render.mjs ProductOverview60 --audio-only
import { chromium } from 'playwright';
import { spawn, spawnSync, execFileSync } from 'node:child_process';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// comps.js is a plain browser script; evaluate it in a sandbox to read the same data.
const sandbox = {};
vm.runInNewContext(fs.readFileSync(path.join(ROOT, 'engine', 'comps.js'), 'utf8'), sandbox);
const COMPS = sandbox.COMPS;

const args = process.argv.slice(2);
const name = args[0];
const opt = (k, d) => { const i = args.indexOf(`--${k}`); return i >= 0 ? args[i + 1] : d; };
const flag = (k) => args.includes(`--${k}`);
const comp = COMPS[name];
if (!comp) { console.error(`Unknown composition ${name}. Use: ${Object.keys(COMPS).join(', ')}`); process.exit(1); }

const OUT = path.join(ROOT, 'out');
const WORK = path.join(ROOT, 'out', 'work');
fs.mkdirSync(WORK, { recursive: true });
const FPS = comp.fps || 30;
const FILE_BASE = { ProductOverview60: 'product-overview-60s', ProductWalkthrough90: 'product-walkthrough-90s' }[name] || name;

/* ── tiny static server for the stage ─────────────── */
const TYPES = { '.html': 'text/html', '.js': 'application/javascript', '.png': 'image/png', '.json': 'application/json', '.css': 'text/css' };
function serve() {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const p = path.join(ROOT, decodeURIComponent(new URL(req.url, 'http://x').pathname));
      if (!p.startsWith(ROOT) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(p)] || 'application/octet-stream' });
      fs.createReadStream(p).pipe(res);
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

async function openStage(srv) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on('pageerror', (e) => console.error('[stage error]', e.message));
  await page.goto(`http://127.0.0.1:${srv.address().port}/engine/stage.html?comp=${name}`);
  await page.evaluate(() => window.stageReady);
  return { browser, page };
}

async function stills(times) {
  const srv = await serve();
  const { browser, page } = await openStage(srv);
  const dir = path.join(OUT, 'stills', name);
  fs.mkdirSync(dir, { recursive: true });
  for (const t of times) {
    await page.evaluate((tt) => window.renderAt(tt), t);
    const file = path.join(dir, `${String(t).padStart(5, '0')}.png`);
    await page.locator('#stage').screenshot({ path: file });
    console.log('still', file);
  }
  await browser.close();
  srv.close();
}

async function video(from, to, file) {
  const srv = await serve();
  const { browser, page } = await openStage(srv);
  const f0 = Math.round(from * FPS), f1 = Math.round(to * FPS);
  const ff = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
    '-vf', 'scale=in_range=pc:out_range=tv,format=yuv420p', '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '16', '-r', String(FPS), '-movflags', '+faststart', file], { stdio: ['pipe', 'inherit', 'inherit'] });
  const stage = page.locator('#stage');
  const t0 = Date.now();
  for (let f = f0; f < f1; f++) {
    await page.evaluate((tt) => window.renderAt(tt), f / FPS);
    const buf = await stage.screenshot({ type: 'jpeg', quality: 95 });
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
    if ((f - f0) % 150 === 0) console.log(`frame ${f}/${f1}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  }
  ff.stdin.end();
  await new Promise((r) => ff.on('close', r));
  await browser.close();
  srv.close();
}

/* ── audio ─────────────────────────────────────────── */
function sfx() {
  const click = path.join(WORK, 'click.wav');
  const whoosh = path.join(WORK, 'whoosh.wav');
  if (!fs.existsSync(click)) {
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
      "aevalsrc='0.55*sin(2*PI*2400*t)*exp(-t*140)+0.3*(random(0)*2-1)*exp(-t*380)':s=48000:d=0.07",
      '-af', 'highpass=f=700,lowpass=f=7000,volume=0.6', '-ac', '2', click]);
  }
  if (!fs.existsSync(whoosh)) {
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'anoisesrc=d=1.4:c=pink:a=0.5:r=48000',
      '-af', 'bandpass=f=900:width_type=o:w=2,afade=t=in:d=0.7:curve=qsin,afade=t=out:st=0.7:d=0.7:curve=qsin,volume=0.35', '-ac', '2', whoosh]);
  }
  return { click, whoosh };
}

function clickTimes() {
  const out = [];
  comp.scenes.forEach((s) => (s.cursor || []).forEach((p) => { if (p.click) out.push(s.start + p.t); }));
  return out;
}

function mixAudio(file) {
  const { click, whoosh } = sfx();
  const prep = path.join(WORK, 'prep');
  fs.mkdirSync(prep, { recursive: true });
  const rel = (p) => path.relative(ROOT, p);
  // 1. Each narration line → speech level (Kokoro renders around −25 LUFS), 48 kHz stereo.
  const vo = comp.vo.map((v) => {
    const src = path.join(ROOT, 'assets', 'audio', 'vo', `${v.id}.wav`);
    const dst = path.join(prep, `${v.id}.wav`);
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', src, '-af', 'aresample=48000,loudnorm=I=-16:TP=-2:LRA=7,aresample=48000', '-ar', '48000', '-ac', '2', dst]);
    return { file: rel(dst), at: v.at };
  });
  const music = path.join(prep, `music-${comp.duration}.wav`);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', path.join(ROOT, 'assets', 'audio', `music-${comp.duration}.wav`), '-ar', '48000', '-ac', '2', music]);
  const fx = [...clickTimes().map((at) => ({ file: rel(click), at })), ...(comp.whoosh || []).map((at) => ({ file: rel(whoosh), at }))];
  // 2. Sample-accurate placement + ducking (numpy, inside the local Kokoro container).
  //    Music ≈ −25 LUFS between lines and about 9 dB lower under speech.
  const plan = { duration: comp.duration, sr: 48000, music: rel(music), music_gain_db: -9, duck_db: -9, vo, fx };
  const planFile = path.join(prep, `${FILE_BASE}-plan.json`);
  fs.writeFileSync(planFile, JSON.stringify(plan, null, 1));
  const pre = path.join(WORK, `${FILE_BASE}-premix.wav`);
  execFileSync('docker', ['run', '--rm', '-v', `${ROOT}:/work`, 'pitchmirror-kokoro', 'python', 'tts/mix.py', rel(planFile), rel(pre)], { stdio: ['ignore', 'inherit', 'inherit'] });
  // 3. Two-pass loudness normalisation to −16 LUFS, true peak −1.5 dBTP.
  const meas = spawnSync('ffmpeg', ['-hide_banner', '-i', pre, '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'], { encoding: 'utf8' }).stderr;
  const j0 = meas.lastIndexOf('{');
  const m = JSON.parse(meas.slice(j0, meas.indexOf('}', j0) + 1));
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', pre, '-af',
    `loudnorm=I=-16:TP=-1.5:LRA=11:measured_I=${m.input_i}:measured_TP=${m.input_tp}:measured_LRA=${m.input_lra}:measured_thresh=${m.input_thresh}:offset=${m.target_offset}:linear=true,aresample=48000`,
    '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', file]);
  return file;
}

/* ── main ──────────────────────────────────────────── */
if (flag('stills')) {
  await stills(opt('stills').split(',').map(Number));
  process.exit(0);
}
const audio = path.join(WORK, `${FILE_BASE}.wav`);
mixAudio(audio);
console.log('audio mixed', audio);
if (flag('audio-only')) process.exit(0);

const from = Number(opt('from', 0));
const to = Number(opt('to', comp.duration));
const partial = from > 0 || to < comp.duration;
const silent = path.join(WORK, `${FILE_BASE}${partial ? `-${from}-${to}` : ''}-video.mp4`);
await video(from, to, silent);
const final = partial ? path.join(OUT, `${FILE_BASE}-preview-${from}-${to}.mp4`) : path.join(OUT, `${FILE_BASE}.mp4`);
execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', silent, '-ss', String(from), '-t', String(to - from), '-i', audio,
  '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-shortest', '-movflags', '+faststart', final]);
console.log('wrote', final);
