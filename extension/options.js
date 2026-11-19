/** Pitchlight extension — settings page. */
import { DEFAULTS, getSettings, isBuiltInOrigin } from './settings.js';

const $ = (id) => document.getElementById(id);
const form = $('form');

function status(text, kind = '') {
  $('status').textContent = text;
  $('status').className = `status ${kind}`;
}

function parseOrigin(value) {
  const u = new URL(value.trim());
  if (u.protocol !== 'http:' && u.protocol !== 'https:') throw new Error('Use an http:// or https:// address.');
  return u.origin;
}

async function load() {
  const s = await getSettings();
  $('server').value = s.serverUrl;
  const radio = form.querySelector(`input[name="openIn"][value="${s.openIn}"]`)
    || form.querySelector(`input[name="openIn"][value="${DEFAULTS.openIn}"]`);
  radio.checked = true;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  let origin;
  try { origin = parseOrigin($('server').value); } catch (err) { status(err.message || 'That address isn\'t valid.', 'warn'); return; }
  if (!isBuiltInOrigin(origin)) {
    // Must run inside the click: Chrome only shows the permission prompt for a user gesture.
    const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
    if (!granted) { status('Chrome needs permission to reach that server. Nothing was saved.', 'warn'); return; }
  }
  const openIn = form.querySelector('input[name="openIn"]:checked')?.value || DEFAULTS.openIn;
  await chrome.storage.sync.set({ serverUrl: origin, openIn });
  const res = await chrome.runtime.sendMessage({ target: 'sw', type: 'settings_changed' });
  $('server').value = origin;
  if (res && !res.ok) status(`Saved, but: ${res.error}`, 'warn');
  else status('Saved. Reload any open Pitchlight page.', 'ok');
});

$('test').addEventListener('click', async () => {
  let origin;
  try { origin = parseOrigin($('server').value); } catch (err) { status(err.message, 'warn'); return; }
  status('Checking…');
  try {
    const res = await fetch(`${origin}/health`, { cache: 'no-store' });
    status(res.ok ? 'Connected ✓' : `The server answered with ${res.status}.`, res.ok ? 'ok' : 'warn');
  } catch (_) {
    status(isBuiltInOrigin(origin) ? 'Can\'t reach it. Is the server running?' : 'Can\'t reach it. Save first so Chrome can ask for permission, then test.', 'warn');
  }
});

chrome.commands.getAll().then((commands) => {
  const cmd = commands.find((c) => c.name === 'start-coaching');
  $('shortcut').textContent = cmd?.shortcut || 'no shortcut set';
});

load();
