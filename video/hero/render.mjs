// Renders hero.html (a function of time) to frames with Playwright, encodes with FFmpeg.
//   node hero/render.mjs            → ../../frontend/media/hero.{mp4,webm}, hero-mobile.mp4, hero-poster.jpg
//   node hero/render.mjs --stills 2,5,8,10   → out/hero-stills/*.png (review frames)
import { chromium } from 'playwright';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, '../../frontend/media');
const STILLS = path.resolve(HERE, '../out/hero-stills');
const args = process.argv.slice(2);
const FPS = 30, DUR = 12;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
page.on('pageerror', (e) => console.error('[page]', e.message));
await page.goto('file://' + path.join(HERE, 'hero.html'));
await page.waitForFunction(() => window.heroReady === true, null, { timeout: 30000 });
await page.waitForTimeout(500);

if (args[0] === '--stills') {
  fs.mkdirSync(STILLS, { recursive: true });
  for (const t of args[1].split(',').map(Number)) {
    await page.evaluate((x) => window.seek(x), t);
    await page.screenshot({ path: path.join(STILLS, `t${String(t).replace('.', '_')}.png`) });
  }
  await browser.close();
  process.exit(0);
}

fs.mkdirSync(OUT, { recursive: true });
const frames = FPS * DUR;
const enc = (outfile, extra, vf) => new Promise((resolve, reject) => {
  const p = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-', '-vf', vf, ...extra, '-an', outfile], { stdio: ['pipe', 'inherit', 'inherit'] });
  p.on('close', (c) => (c ? reject(new Error('ffmpeg ' + c)) : resolve()));
  enc.last = p;
});
const jobs = [
  ['hero.mp4', ['-c:v', 'libx264', '-preset', 'slow', '-crf', '27', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-tune', 'grain'], 'scale=1920:1080:flags=lanczos'],
  ['hero-mobile.mp4', ['-c:v', 'libx264', '-preset', 'slow', '-crf', '30', '-pix_fmt', 'yuv420p', '-movflags', '+faststart'], 'scale=960:540:flags=lanczos'],
  ['hero.webm', ['-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '38', '-row-mt', '1', '-pix_fmt', 'yuv420p'], 'scale=1600:900:flags=lanczos'],
];
const procs = jobs.map(([f, extra, vf]) => {
  const p = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-', '-vf', vf, ...extra, '-an', path.join(OUT, f)], { stdio: ['pipe', 'inherit', 'inherit'] });
  const done = new Promise((res, rej) => p.on('close', (c) => (c ? rej(new Error(f + ' ' + c)) : res())));
  return { p, done };
});
for (let i = 0; i < frames; i++) {
  await page.evaluate((x) => window.seek(x), i / FPS);
  const png = await page.screenshot({ type: 'png' });
  for (const { p } of procs) if (!p.stdin.write(png)) await new Promise((r) => p.stdin.once('drain', r));
  if (i % 60 === 0) console.log('frame', i, '/', frames);
}
for (const { p } of procs) p.stdin.end();
await Promise.all(procs.map((x) => x.done));
await page.evaluate(() => window.seek(8.6));
await page.screenshot({ path: path.join(OUT, 'hero-poster.jpg'), type: 'jpeg', quality: 82 });
await browser.close();
console.log('done');
