/** Pitchlight Home: next steps (practice → prepare → meet), shortcuts and recent calls. */
(function () {
  'use strict';
  const { api, esc, $ } = window.PL;
  const settle = (p) => p.then((v) => v, () => null);

  async function run() {
    const [me, kit, scripts, docs, sessions, company] = await Promise.all([
      settle(fetch('/auth/me', { credentials: 'same-origin' }).then((r) => r.json())),
      settle(api('/api/demo-kit')), settle(api('/api/scripts')), settle(api('/api/library')),
      settle(api('/api/sessions?limit=6')), settle(api('/api/company')),
    ]);

    const name = ((me && me.user && me.user.name) || '').split(' ')[0];
    $('hm-title').textContent = name ? `Welcome back, ${name}` : 'Welcome to Pitchlight';

    const practiced = !!(kit && kit.completed);
    const nScripts = scripts ? scripts.scripts.length : 0;
    const nDocs = docs ? docs.docs.length : 0;
    const prepared = nScripts > 0;
    const done = { 1: practiced, 2: prepared, 3: false };
    const next = [1, 2, 3].find((n) => !done[n]) || 3;
    [1, 2, 3].forEach((n) => {
      const el = $(`hm-step-${n}`);
      el.classList.toggle('is-done', !!done[n]);
      el.classList.toggle('is-next', n === next);
      if (done[n]) el.querySelector('.pl-step-num').textContent = '✓';
      const btn = el.querySelector('.pl-btn');
      btn.classList.toggle('is-accent', n === next);
      btn.classList.toggle('is-ghost', n !== next);
    });
    if (practiced) { $('hm-s1-text').textContent = 'Done. You have seen how every feature works. Run it again any time before an important call.'; $('hm-s1-btn').textContent = 'Run it again'; }
    if (prepared) $('hm-s2-text').textContent = `${nScripts} script${nScripts === 1 ? '' : 's'} and ${nDocs} document${nDocs === 1 ? '' : 's'} saved. Prepare another for your next client.`;
    if (company) {
      const biz = company.ventures.filter((v) => v.name !== 'General').length;
      if (biz) $('hm-company-text').textContent = `${biz} business${biz === 1 ? '' : 'es'} in the hub. The coach answers from them when your own notes don't cover a question.`;
    }

    const list = (sessions && sessions.sessions) || [];
    $('hm-recent').innerHTML = list.length ? list.map((s) => {
      const when = s.created_at ? new Date(s.created_at * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
      const label = s.practice_meeting ? 'Practice meeting' : (s.client || (s.session_kind === 'meeting' ? 'Meeting' : (s.pitch_name || 'Practice session')));
      const mins = Math.max(1, Math.round((s.duration_seconds || 0) / 60));
      return `<li><a href="/app?session=${encodeURIComponent(s.session_id)}">${esc(label)}</a>
        <span class="hm-meta">${esc(when)} · ${mins} min${s.practice_meeting ? ' · practice' : ''}</span>
        <span class="hm-score">${s.insufficient_speech ? '–' : Number(s.overall_score || 0).toFixed(0)}</span></li>`;
    }).join('') : '<li class="hm-empty">No calls yet. Your first practice meeting will show up here.</li>';
  }
  run();
})();
