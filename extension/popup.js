/** Pitchlight extension — toolbar popup: start / stop listening to the meeting in this tab. */
import { getSettings, serverOrigin, detectPlatform } from './settings.js';

const $ = (id) => document.getElementById(id);
let tab = null;

function show(el, on) { el.classList.toggle('hidden', !on); }

function message(text, kind = '') {
  const el = $('msg');
  el.textContent = text || '';
  el.className = `msg ${kind}`;
  show(el, !!text);
}

function screenInfo() {
  return {
    availWidth: window.screen.availWidth,
    availHeight: window.screen.availHeight,
    availLeft: window.screen.availLeft || 0,
    availTop: window.screen.availTop || 0,
  };
}

async function serverReachable(origin) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 3000);
  try {
    const res = await fetch(`${origin}/health`, { cache: 'no-store', signal: ctrl.signal });
    return res.ok;
  } catch (_) {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function render() {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const { serverUrl } = await getSettings();
  const origin = serverOrigin(serverUrl);
  const { capture } = (await chrome.runtime.sendMessage({ target: 'sw', type: 'get_state' })) || {};

  const url = tab?.url || '';
  const platform = detectPlatform(url);
  const isWeb = /^https?:/i.test(url);
  const isPitchlight = isWeb && new URL(url).origin === origin;
  const here = capture && capture.tabId === tab?.id;

  $('tab-title').textContent = here ? capture.title : (tab?.title || 'Unknown tab');
  $('tab-host').textContent = isWeb ? new URL(url).host : '';
  $('tab-kind').textContent = here ? 'Listening to this meeting'
    : (platform.id !== 'other' ? `${platform.name} detected` : 'This tab');
  show($('live-dot'), !!here);

  const start = $('start');
  show(start, !here);
  show($('open'), !!capture);
  show($('stop'), !!capture);
  start.textContent = capture ? 'Listen to this tab instead' : 'Start coaching on this tab';

  if (here) {
    message(capture.playback === 'running' || capture.playback === 'unknown'
      ? 'The coach hears this meeting. Keep the Pitchlight window open and press Start meeting coach there.'
      : 'Listening, but Chrome may have muted this tab for you. If you can\'t hear the meeting, stop and start again.',
    capture.playback === 'running' || capture.playback === 'unknown' ? 'ok' : 'warn');
  } else if (capture) {
    message(`Already listening to “${capture.title}”.`);
  } else if (isPitchlight) {
    message('This is Pitchlight itself. Switch to your meeting tab and click the Pitchlight icon there.');
  } else if (!isWeb) {
    message('Open your meeting in a browser tab (Google Meet, Teams or Zoom on the web), then click the icon there.');
  } else {
    message('');
  }

  const line = $('server-line');
  const reachable = await serverReachable(origin);
  line.textContent = reachable
    ? `Pitchlight server: ${new URL(origin).host} ✓`
    : `Can't reach Pitchlight at ${origin}. Start the server, or change the address in Settings.`;
  line.className = `server ${reachable ? 'ok' : 'warn'}`;
  start.disabled = !reachable || !isWeb || isPitchlight;
}

$('start').addEventListener('click', async () => {
  const btn = $('start');
  btn.disabled = true;
  btn.textContent = 'Starting…';
  const res = await chrome.runtime.sendMessage({ target: 'sw', type: 'start', tabId: tab.id, screen: screenInfo() });
  if (res?.ok) { window.close(); return; }
  message(res?.error || 'Could not start.', 'warn');
  await render();
});

$('open').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({ target: 'sw', type: 'open_pitchlight', screen: screenInfo() });
  window.close();
});

$('stop').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({ target: 'sw', type: 'stop' });
  await render();
});

$('settings').addEventListener('click', () => chrome.runtime.openOptionsPage());

chrome.commands.getAll().then((commands) => {
  const cmd = commands.find((c) => c.name === 'start-coaching');
  $('shortcut').textContent = cmd?.shortcut || 'not set';
});

render().catch((err) => message(String(err?.message || err), 'warn'));
