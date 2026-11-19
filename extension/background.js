/**
 * Pitchlight extension — service worker.
 *
 * Start (popup button or Alt+Shift+P on the meeting tab):
 *   1. chrome.tabCapture gives a stream id for the meeting tab (allowed because the user
 *      just invoked the extension on that tab).
 *   2. The offscreen document turns it into audio, keeps playing it to the user (capturing a
 *      tab mutes it otherwise) and posts 100 ms chunks of 16 kHz mono PCM back here.
 *   3. This worker relays the chunks to every open Pitchlight page through the bridge content
 *      script; the page feeds them into its normal "meeting audio" pipeline (frontend/js/ext-bridge.js).
 *   4. The Pitchlight meeting page opens (or is focused) next to the meeting.
 * Stop: popup, the Pitchlight page ending the meeting, or the meeting tab closing.
 */
import { getSettings, serverOrigin, isBuiltInOrigin, detectPlatform } from './settings.js';

const PAGE_PORT = 'pitchlight-page';
const OFFSCREEN_PATH = 'offscreen.html';
const CUSTOM_BRIDGE_ID = 'pitchlight-bridge-custom';
const WINDOW_WIDTH = 480;

const pagePorts = new Set();
let creatingOffscreen = null;

/* ── Capture state (survives the worker being suspended) ─────────── */

async function getCapture() {
  const { capture } = await chrome.storage.session.get('capture');
  return capture || null;
}

async function setCapture(capture, reason = '') {
  await chrome.storage.session.set({ capture });
  await updateBadge(capture);
  broadcast(statusMessage(capture, reason));
}

function statusMessage(capture, reason = '') {
  if (!capture) return { type: 'status', capturing: false, reason };
  return {
    type: 'status',
    capturing: true,
    title: capture.title,
    host: capture.host,
    platform: capture.platform,
    platformName: capture.platformName,
    startedAt: capture.startedAt,
    playback: capture.playback,
  };
}

async function updateBadge(capture) {
  await chrome.action.setBadgeText({ text: capture ? 'ON' : '' });
  if (capture) await chrome.action.setBadgeBackgroundColor({ color: '#dc2626' });
  await chrome.action.setTitle({ title: capture ? `Pitchlight — listening to ${capture.title}` : 'Pitchlight' });
}

async function flashError(message) {
  await chrome.action.setBadgeText({ text: '!' });
  await chrome.action.setBadgeBackgroundColor({ color: '#b45309' });
  await chrome.action.setTitle({ title: `Pitchlight — ${message}` });
  setTimeout(async () => updateBadge(await getCapture()), 8000);
}

function broadcast(message) {
  for (const port of pagePorts) {
    try { port.postMessage(message); } catch (_) { pagePorts.delete(port); }
  }
}

/* ── Offscreen document ──────────────────────────────────────────── */

async function hasOffscreen() {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'],
    documentUrls: [chrome.runtime.getURL(OFFSCREEN_PATH)],
  });
  return contexts.length > 0;
}

async function ensureOffscreen() {
  if (await hasOffscreen()) return;
  if (!creatingOffscreen) {
    creatingOffscreen = chrome.offscreen.createDocument({
      url: OFFSCREEN_PATH,
      reasons: ['USER_MEDIA'],
      justification: 'Capture the meeting tab\'s audio so the Pitchlight coach can hear the other person.',
    }).finally(() => { creatingOffscreen = null; });
  }
  await creatingOffscreen;
}

async function closeOffscreen() {
  if (await hasOffscreen()) {
    try { await chrome.offscreen.closeDocument(); } catch (_) { /* already closing */ }
  }
}

/* ── Start / stop ────────────────────────────────────────────────── */

function hostOf(url) {
  try { return new URL(url).host; } catch (_) { return ''; }
}

function friendlyError(err) {
  const msg = String(err?.message || err || '');
  if (/not been invoked|activeTab/i.test(msg)) {
    return 'Chrome only lets Pitchlight listen after you click its icon on the meeting tab. Go to the meeting tab, click the Pitchlight icon, then Start.';
  }
  if (/active stream/i.test(msg)) {
    return 'This tab is already being captured (by a screen share or another extension). Stop that first.';
  }
  if (/chrome pages|chrome:\/\//i.test(msg)) return 'Chrome\'s own pages can\'t be captured. Open your meeting tab.';
  return msg || 'Something went wrong.';
}

async function startCapture(tab, screen) {
  if (!tab || tab.id == null) throw new Error('No tab to listen to.');
  const { serverUrl } = await getSettings();
  const origin = serverOrigin(serverUrl);
  const url = tab.url || '';
  if (url && !/^https?:/i.test(url)) throw new Error('Open your meeting in a normal browser tab (Google Meet, Teams or Zoom on the web).');
  if (url && hostOf(url) && new URL(url).origin === origin) {
    throw new Error('This is the Pitchlight tab. Switch to your meeting tab, then start Pitchlight there.');
  }

  const current = await getCapture();
  if (current && current.tabId === tab.id && await hasOffscreen()) {
    await openPitchlight(current, screen, tab.windowId);
    return current;
  }
  if (current) await stopCapture('switched');

  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  await ensureOffscreen();
  const res = await chrome.runtime.sendMessage({ target: 'offscreen', type: 'start', streamId });
  if (!res || !res.ok) {
    await closeOffscreen();
    throw new Error(res?.error || 'Chrome would not share this tab\'s audio.');
  }

  const platform = detectPlatform(url);
  const capture = {
    tabId: tab.id,
    windowId: tab.windowId,
    title: (tab.title || platform.name).slice(0, 120),
    host: hostOf(url),
    platform: platform.id,
    platformName: platform.name,
    startedAt: Date.now(),
    playback: res.playback || 'unknown',   // 'running' = the user still hears the meeting
  };
  await setCapture(capture);
  try {
    await openPitchlight(capture, screen, tab.windowId);
  } catch (err) {
    console.warn('Pitchlight: listening, but the Pitchlight page did not open', err);   // popup's "Open Pitchlight" still works
  }
  return capture;
}

async function stopCapture(reason = 'stopped') {
  if (await hasOffscreen()) {
    try { await chrome.runtime.sendMessage({ target: 'offscreen', type: 'stop' }); } catch (_) { /* gone */ }
    await closeOffscreen();
  }
  await setCapture(null, reason);
}

/** Focus an open Pitchlight meeting page, or open one beside the meeting. */
async function openPitchlight(capture, screen, meetingWindowId) {
  const { serverUrl, openIn } = await getSettings();
  const origin = serverOrigin(serverUrl);
  let open = [];
  try { open = await chrome.tabs.query({ url: `${origin}/meeting*` }); } catch (_) { /* no permission yet */ }
  if (open.length) {
    const t = open[0];
    await chrome.tabs.update(t.id, { active: true });
    await chrome.windows.update(t.windowId, { focused: true });
    return;
  }

  const target = new URL('/meeting', origin);
  target.searchParams.set('from', 'extension');
  if (capture?.platform) target.searchParams.set('platform', capture.platform);

  if (openIn === 'tab') {
    await chrome.tabs.create({ url: target.href, windowId: meetingWindowId });
    return;
  }

  const bounds = {};
  if (screen?.availWidth) {
    bounds.left = Math.round((screen.availLeft || 0) + screen.availWidth - WINDOW_WIDTH - 12);
    bounds.top = Math.round((screen.availTop || 0) + 24);
    bounds.height = Math.round(Math.min(920, screen.availHeight - 48));
  } else if (meetingWindowId != null) {
    try {
      const w = await chrome.windows.get(meetingWindowId);
      bounds.left = Math.max(0, w.left + w.width - WINDOW_WIDTH - 12);
      bounds.top = Math.max(0, w.top + 60);
      bounds.height = Math.max(500, Math.min(920, w.height - 80));
    } catch (_) { /* let Chrome place it */ }
  }
  // Chrome rejects bounds that are mostly off-screen (small or rearranged displays): retry without them,
  // and fall back to a tab so a placement problem never blocks the meeting.
  try {
    await chrome.windows.create({ url: target.href, type: 'popup', width: WINDOW_WIDTH, focused: true, ...bounds });
  } catch (_) {
    try {
      await chrome.windows.create({ url: target.href, type: 'popup', width: WINDOW_WIDTH, focused: true });
    } catch (__) {
      await chrome.tabs.create({ url: target.href });
    }
  }
}

/* ── Content script for a custom (non-localhost) server ──────────── */

async function syncCustomBridge() {
  const { serverUrl } = await getSettings();
  const origin = serverOrigin(serverUrl);
  const registered = await chrome.scripting.getRegisteredContentScripts({ ids: [CUSTOM_BRIDGE_ID] });
  if (registered.length) await chrome.scripting.unregisterContentScripts({ ids: [CUSTOM_BRIDGE_ID] });
  if (isBuiltInOrigin(origin)) return;
  const pattern = `${origin}/*`;
  if (!(await chrome.permissions.contains({ origins: [pattern] }))) return;
  await chrome.scripting.registerContentScripts([{
    id: CUSTOM_BRIDGE_ID, matches: [pattern], js: ['bridge.js'], runAt: 'document_start', persistAcrossSessions: true,
  }]);
}

/* ── Wiring ──────────────────────────────────────────────────────── */

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.target !== 'sw' || sender.id !== chrome.runtime.id) return undefined;
  switch (msg.type) {
    case 'pcm':
      broadcast({ type: 'pcm', rate: msg.rate, seq: msg.seq, data: msg.data });
      return undefined;
    case 'ended':                               // the meeting tab closed or stopped being capturable
      stopCapture('tab_closed');
      return undefined;
    case 'get_state':
      getCapture().then((capture) => sendResponse({ capture }));
      return true;
    case 'start':
      chrome.tabs.get(msg.tabId)
        .then((tab) => startCapture(tab, msg.screen))
        .then((capture) => sendResponse({ ok: true, capture }), (err) => sendResponse({ ok: false, error: friendlyError(err) }));
      return true;
    case 'stop':
      stopCapture('user').then(() => sendResponse({ ok: true }));
      return true;
    case 'open_pitchlight':
      getCapture().then((c) => openPitchlight(c, msg.screen, c?.windowId)).then(() => sendResponse({ ok: true }),
        (err) => sendResponse({ ok: false, error: friendlyError(err) }));
      return true;
    case 'settings_changed':
      syncCustomBridge().then(() => sendResponse({ ok: true }), (err) => sendResponse({ ok: false, error: friendlyError(err) }));
      return true;
    default:
      return undefined;
  }
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PAGE_PORT) return;
  // Only the configured Pitchlight server's pages may receive meeting audio.
  const allowed = getSettings().then(({ serverUrl }) => {
    const senderOrigin = port.sender?.origin || (port.sender?.url ? new URL(port.sender.url).origin : '');
    return senderOrigin === serverOrigin(serverUrl);
  });
  port.onMessage.addListener(async (m) => {
    if (!(await allowed) || !m) return;
    if (m.type === 'hello') port.postMessage(statusMessage(await getCapture()));
    else if (m.type === 'stop_audio') stopCapture('stopped_in_pitchlight');
    else if (m.type === 'meeting_ended') stopCapture('meeting_ended');
  });
  port.onDisconnect.addListener(() => pagePorts.delete(port));
  allowed.then(async (ok) => {
    if (!ok) { port.disconnect(); return; }
    pagePorts.add(port);
    port.postMessage(statusMessage(await getCapture()));
  });
});

chrome.commands.onCommand.addListener((command, tab) => {
  if (command !== 'start-coaching') return;
  (async () => {
    const target = tab?.id != null ? tab : (await chrome.tabs.query({ active: true, lastFocusedWindow: true }))[0];
    await startCapture(target);
  })().catch((err) => flashError(friendlyError(err)));
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const capture = await getCapture();
  if (capture && capture.tabId === tabId) stopCapture('tab_closed');
});

chrome.runtime.onInstalled.addListener(() => { syncCustomBridge().catch(() => {}); });

// After a browser restart or an extension reload the offscreen document is gone: clear stale state.
(async () => {
  const capture = await getCapture();
  if (capture && !(await hasOffscreen())) await setCapture(null, 'restarted');
  else await updateBadge(capture);
})();

// Handy from the service worker console (and used by the end-to-end test).
globalThis.pitchlight = { startCapture, stopCapture, getCapture, openPitchlight };
