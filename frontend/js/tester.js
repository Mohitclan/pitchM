/**
 * Pitchlight — "Who's testing?" profiles for the pilot.
 *
 * During the pilot several colleagues use the same laptop/browser. Each tester gets
 * their own user id, so History, scores and feedback stay separate. Must load BEFORE
 * app.js / meeting.js, which read the current id from localStorage at startup.
 *
 * Renders a small picker into any element with id="tester-slot".
 */
(function () {
  'use strict';

  const LIST_KEY = 'pitchmirror_testers';
  const ID_KEY = 'pitchmirror_user_id';
  const ID_RE = /^[A-Za-z0-9_.-]{3,64}$/;

  function newId() {
    return `user-${(window.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)).slice(0, 12)}`;
  }

  function load() {
    let list = [];
    try { list = JSON.parse(localStorage.getItem(LIST_KEY) || '[]'); } catch (_) { list = []; }
    list = Array.isArray(list) ? list.filter((t) => t && ID_RE.test(t.id || '') && t.name) : [];
    let currentId = localStorage.getItem(ID_KEY);
    if (!list.length) {
      // First run: adopt the existing browser id so earlier sessions stay visible.
      const id = currentId && ID_RE.test(currentId) ? currentId : newId();
      list = [{ id, name: 'Me' }];
    }
    if (!currentId || !list.some((t) => t.id === currentId)) currentId = list[0].id;
    try {
      localStorage.setItem(LIST_KEY, JSON.stringify(list));
      localStorage.setItem(ID_KEY, currentId);
    } catch (_) { /* storage blocked — app falls back to its own id */ }
    return { list, currentId };
  }

  let state = load();

  function current() {
    return state.list.find((t) => t.id === state.currentId) || state.list[0];
  }

  function save() {
    try {
      localStorage.setItem(LIST_KEY, JSON.stringify(state.list));
      localStorage.setItem(ID_KEY, state.currentId);
    } catch (_) { /* ignore */ }
  }

  function switchTo(id) {
    if (id === state.currentId) return;
    if (window.__pmSessionActive && !window.confirm('A session is running. Switching tester ends it. Continue?')) return;
    state.currentId = id;
    save();
    location.reload();   // app/meeting read the id at startup
  }

  function addTester() {
    const name = (window.prompt('New tester name (e.g. Priya)') || '').trim().slice(0, 40);
    if (!name) return;
    const t = { id: newId(), name };
    state.list.push(t);
    save();
    switchTo(t.id);
  }

  function renameCurrent() {
    const t = current();
    const name = (window.prompt('Rename tester', t.name) || '').trim().slice(0, 40);
    if (!name) return;
    t.name = name;
    save();
    render();
  }

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function render() {
    const slot = document.getElementById('tester-slot');
    if (!slot) return;
    const t = current();
    slot.innerHTML = `
      <div class="tester-picker">
        <button type="button" class="tester-btn" aria-haspopup="true" aria-expanded="false" title="Who's testing?">
          <span class="tester-dot" aria-hidden="true">${esc(t.name.slice(0, 1).toUpperCase())}</span>
          <span class="tester-name">${esc(t.name)}</span><span aria-hidden="true">▾</span>
        </button>
        <div class="tester-menu" role="menu" hidden>
          <div class="tester-menu-title">Who's testing?</div>
          ${state.list.map((x) => `
            <button type="button" role="menuitem" class="tester-item${x.id === t.id ? ' is-current' : ''}" data-id="${esc(x.id)}">
              ${x.id === t.id ? '✓ ' : ''}${esc(x.name)}
            </button>`).join('')}
          <hr />
          <button type="button" role="menuitem" class="tester-item" data-action="add">+ New tester</button>
          <button type="button" role="menuitem" class="tester-item" data-action="rename">Rename “${esc(t.name)}”</button>
          <a role="menuitem" class="tester-item" href="/pilot">Pilot feedback summary</a>
          <a role="menuitem" class="tester-item" href="/guide">How to try it</a>
        </div>
      </div>`;
    const btn = slot.querySelector('.tester-btn');
    const menu = slot.querySelector('.tester-menu');
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute('aria-expanded', String(!menu.hidden));
    });
    document.addEventListener('click', () => { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); });
    menu.addEventListener('click', (e) => {
      const item = e.target.closest('[data-id], [data-action]');
      if (!item) return;
      e.stopPropagation();
      if (item.dataset.id) switchTo(item.dataset.id);
      else if (item.dataset.action === 'add') addTester();
      else if (item.dataset.action === 'rename') renameCurrent();
    });
  }

  /* ── Accounts mode: the signed-in person replaces the "Who's testing?" picker ── */
  function renderAccount(user) {
    const slot = document.getElementById('tester-slot');
    if (!slot) return;
    slot.innerHTML = `
      <div class="tester-picker">
        <button type="button" class="tester-btn" aria-haspopup="true" aria-expanded="false" title="${esc(user.email)}">
          <span class="tester-dot" aria-hidden="true">${esc(user.name.slice(0, 1).toUpperCase())}</span>
          <span class="tester-name">${esc(user.name)}</span><span aria-hidden="true">▾</span>
        </button>
        <div class="tester-menu" role="menu" hidden>
          <div class="tester-menu-title">${esc(user.email)}</div>
          <a role="menuitem" class="tester-item" href="/app?view=history">My history</a>
          ${user.role === 'admin' ? '<a role="menuitem" class="tester-item" href="/pilot">Pilot feedback summary</a>' : ''}
          <a role="menuitem" class="tester-item" href="/guide">How to try it</a>
          <hr />
          <button type="button" role="menuitem" class="tester-item" data-action="signout">Sign out</button>
        </div>
      </div>`;
    const btn = slot.querySelector('.tester-btn');
    const menu = slot.querySelector('.tester-menu');
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute('aria-expanded', String(!menu.hidden));
    });
    document.addEventListener('click', () => { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); });
    menu.addEventListener('click', async (e) => {
      if (!e.target.closest('[data-action="signout"]')) return;
      if (window.__pmSessionActive && !window.confirm('A session is running. Signing out ends it. Continue?')) return;
      try { await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (_) { /* ignore */ }
      location.href = '/login';
    });
  }

  // If a sign-in expires mid-use, send the person back to the login page instead of showing errors.
  function guardExpiredSignIn() {
    const realFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const resp = await realFetch(...args);
      const url = String(typeof args[0] === 'string' ? args[0] : args[0]?.url || '');
      if (resp.status === 401 && !url.includes('/auth/')) {
        location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
      }
      return resp;
    };
  }

  window.PMTester = { current, render };
  const start = () => {
    fetch('/auth/me', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : { mode: 'off' }))
      .then((info) => {
        if (info.mode === 'accounts') {
          if (!info.user) { location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`; return; }
          window.PMUser = info.user;
          guardExpiredSignIn();
          renderAccount(info.user);
        } else {
          render();
        }
      })
      .catch(() => render());
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
