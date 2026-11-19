/**
 * Settings shared by the service worker, the popup and the options page.
 * (bridge.js is a classic content script and reads `serverUrl` itself.)
 */

export const DEFAULTS = {
  serverUrl: 'http://localhost:8080', // where the Pitchlight server runs
  openIn: 'window',                   // 'window' (small window beside the meeting) or 'tab'
};

export async function getSettings() {
  const saved = await chrome.storage.sync.get(Object.keys(DEFAULTS));
  return { ...DEFAULTS, ...saved };
}

/** Origin (scheme://host[:port]) of a server URL; falls back to the default server. */
export function serverOrigin(url) {
  try {
    const u = new URL(url);
    if (u.protocol === 'http:' || u.protocol === 'https:') return u.origin;
  } catch (_) { /* fall through */ }
  return new URL(DEFAULTS.serverUrl).origin;
}

/** localhost / 127.0.0.1 servers are covered by the manifest; anything else needs a runtime permission. */
export function isBuiltInOrigin(origin) {
  try {
    const u = new URL(origin);
    return u.protocol === 'http:' && (u.hostname === 'localhost' || u.hostname === '127.0.0.1');
  } catch (_) {
    return false;
  }
}

/** Which meeting app a tab URL belongs to. `id` matches the platform options on /meeting. */
export function detectPlatform(url) {
  let host = '';
  try { host = new URL(url).hostname; } catch (_) { return { id: 'other', name: 'This tab' }; }
  const is = (d) => host === d || host.endsWith(`.${d}`);
  if (host === 'meet.google.com') return { id: 'google_meet', name: 'Google Meet' };
  if (is('teams.microsoft.com') || is('teams.live.com') || is('teams.cloud.microsoft')) return { id: 'teams', name: 'Microsoft Teams' };
  if (is('zoom.us') || is('zoom.com') || is('zoomgov.com')) return { id: 'zoom', name: 'Zoom' };
  if (is('webex.com')) return { id: 'webex', name: 'Webex' };
  return { id: 'other', name: host.replace(/^www\./, '') || 'This tab' };
}
