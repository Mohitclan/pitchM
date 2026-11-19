/**
 * Pitchlight extension — bridge content script.
 *
 * Runs on the Pitchlight server's pages only (localhost from the manifest; a custom server is
 * registered at runtime). It carries meeting-audio chunks and capture status from the
 * extension to the page (frontend/js/ext-bridge.js) with window.postMessage, and carries the
 * page's few requests back (stop listening, meeting ended).
 */
(() => {
  'use strict';
  if (window.top !== window) return;

  const FROM_EXT = 'pitchlight-ext';
  const FROM_PAGE = 'pitchlight-page';
  const DEFAULT_SERVER = 'http://localhost:8080';
  const VERSION = chrome.runtime.getManifest().version;
  const PAGE_REQUESTS = new Set(['hello', 'stop_audio', 'meeting_started', 'meeting_ended']);

  let enabled = false;
  let port = null;
  let retries = 0;

  function toPage(msg, transfer) {
    window.postMessage({ ...msg, source: FROM_EXT }, location.origin, transfer || []);
  }

  function toBuffer(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }

  function send(msg) {
    try { port?.postMessage(msg); } catch (_) { /* reconnecting */ }
  }

  function connect() {
    try {
      port = chrome.runtime.connect({ name: 'pitchlight-page' });
    } catch (_) {
      // The extension was reloaded or removed: this copy of the script is orphaned.
      enabled = false;
      toPage({ type: 'status', capturing: false, reason: 'extension_reloaded' });
      return;
    }
    port.onMessage.addListener((m) => {
      retries = 0;
      if (m?.type === 'pcm' && typeof m.data === 'string') {
        const pcm = toBuffer(m.data);
        toPage({ type: 'pcm', rate: m.rate, seq: m.seq, pcm }, [pcm]);
      } else if (m?.type === 'status') {
        toPage(m);
      }
    });
    port.onDisconnect.addListener(() => {
      void chrome.runtime.lastError;
      port = null;
      if (enabled) setTimeout(connect, Math.min(5000, 200 * 2 ** retries++));
    });
    send({ type: 'hello' });
  }

  window.addEventListener('message', (e) => {
    if (!enabled || e.source !== window || e.origin !== location.origin) return;
    const d = e.data;
    if (!d || d.source !== FROM_PAGE || !PAGE_REQUESTS.has(d.type)) return;
    if (d.type === 'hello') toPage({ type: 'hello', version: VERSION });
    send({ type: d.type });
  });

  chrome.storage.sync.get('serverUrl').then(({ serverUrl }) => {
    let origin = DEFAULT_SERVER;
    try { origin = new URL(serverUrl || DEFAULT_SERVER).origin; } catch (_) { /* default */ }
    if (origin !== location.origin) return;   // some other local app: stay out of it
    enabled = true;
    toPage({ type: 'hello', version: VERSION });
    connect();
  });
})();
