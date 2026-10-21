/**
 * Pitchlight shared browser helpers: one copy of the user id, API headers, fetch wrapper and HTML escaping,
 * instead of a copy in every page script. Exposes window.PL.
 */
(function () {
  'use strict';
  const USER_ID_KEY = 'pitchmirror_user_id';

  function userId() {
    try { return localStorage.getItem(USER_ID_KEY) || ''; } catch (_) { return ''; }
  }

  function headers(json) {
    const h = { 'x-user-id': userId() };
    try {
      const token = (localStorage.getItem('pitchmirror_api_token') || '').trim();
      if (token) h.Authorization = `Bearer ${token}`;
    } catch (_) { /* storage blocked */ }
    if (json) h['Content-Type'] = 'application/json';
    return h;
  }

  /** JSON in, JSON out; throws an Error carrying the server's message. */
  async function api(path, opts = {}) {
    const resp = await fetch(path, {
      method: opts.method || 'GET',
      headers: { ...headers(!!opts.json), ...(opts.headers || {}) },
      body: opts.json ? JSON.stringify(opts.json) : opts.body,
    });
    let data = null;
    try { data = await resp.json(); } catch (_) { /* empty body */ }
    if (!resp.ok) throw new Error((data && data.detail) || `Something went wrong (${resp.status})`);
    return data;
  }

  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const $ = (id) => document.getElementById(id);

  window.PL = { userId, headers, api, esc, $ };
})();
