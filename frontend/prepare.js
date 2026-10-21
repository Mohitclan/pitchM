/** Pitchlight — Prepare me: draft a call script from your material, edit it, save it; manage your documents. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const USER_ID = (localStorage.getItem('pitchmirror_user_id') || '');
  const headers = (json) => {
    const h = { 'x-user-id': USER_ID };
    const token = (localStorage.getItem('pitchmirror_api_token') || '').trim();
    if (token) h.Authorization = `Bearer ${token}`;
    if (json) h['Content-Type'] = 'application/json';
    return h;
  };
  async function api(path, opts = {}) {
    const resp = await fetch(path, { ...opts, headers: { ...headers(!!opts.json), ...(opts.headers || {}) }, body: opts.json ? JSON.stringify(opts.json) : opts.body });
    let data = null;
    try { data = await resp.json(); } catch (_) { /* empty */ }
    if (!resp.ok) throw new Error((data && data.detail) || `Something went wrong (${resp.status})`);
    return data;
  }
  const say = (el, text, err) => { el.textContent = text || ''; el.classList.toggle('err', !!err); };

  let prepared = null;      // the draft currently in the editor (carries minutes / client / mode)
  let editingId = null;     // saved script being edited

  /* ── Tabs ── */
  document.querySelectorAll('.pp-tab').forEach((tab) => tab.addEventListener('click', () => {
    document.querySelectorAll('.pp-tab').forEach((t) => t.classList.toggle('is-on', t === tab));
    document.querySelectorAll('.pp-pane').forEach((p) => p.classList.toggle('hidden', p.dataset.pane !== tab.dataset.tab));
  }));

  /* ── Editor <-> payload ── */
  const hardToText = (list) => (list || []).map((q) => `${q.question} :: ${q.answer}`).join('\n');
  const objToText = (list) => (list || []).map((o) => `${o.label}: ${o.answer}`).join('\n');
  const lines = (text) => text.split('\n').map((x) => x.trim()).filter(Boolean);

  function fillEditor(data) {
    prepared = data;
    $('pp-name').value = data.name || '';
    $('pp-script').value = data.text || '';
    $('pp-points').value = (data.key_points || []).join('\n');
    $('pp-hard').value = hardToText(data.hard_questions);
    $('pp-obj').value = objToText(data.objections);
    $('pp-cheat').value = (data.cheat_sheet || []).join('\n');
    const miss = data.missing || [];
    $('pp-missing').classList.toggle('hidden', !miss.length);
    $('pp-missing').innerHTML = miss.length ? `<b>Not in your material — fill these in:</b><ul>${miss.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>` : '';
    $('pp-saved').classList.add('hidden');
    $('pp-result').classList.remove('hidden');
  }

  function readEditor() {
    return {
      name: $('pp-name').value.trim(), text: $('pp-script').value, key_points: lines($('pp-points').value),
      hard_questions: lines($('pp-hard').value).map((l) => { const [q, ...a] = l.split('::'); return { question: q.trim(), answer: a.join('::').trim() }; }).filter((q) => q.question),
      objections: lines($('pp-obj').value).map((l) => { const i = l.indexOf(':'); return i > 0 ? { label: l.slice(0, i).trim(), answer: l.slice(i + 1).trim() } : null; }).filter(Boolean),
      cheat_sheet: lines($('pp-cheat').value),
      minutes: (prepared && prepared.minutes) || 0, client: (prepared && prepared.client) || '',
    };
  }

  /* ── Draft ── */
  $('pp-draft').addEventListener('click', async () => {
    const btn = $('pp-draft');
    btn.disabled = true;
    say($('pp-status'), 'Drafting… this takes about 20–40 seconds.');
    try {
      const data = await api('/api/prepare', { method: 'POST', json: {
        material: $('pp-material').value, client: $('pp-client').value, goal: $('pp-goal').value,
        minutes: Number($('pp-minutes').value), language: $('pp-language').value, use_library: $('pp-use-lib').checked,
      } });
      editingId = null;
      fillEditor(data);
      say($('pp-status'), 'Draft ready — edit anything, then save it.');
      $('pp-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) { say($('pp-status'), e.message, true); }
    btn.disabled = false;
  });

  /* ── Write my own (no AI): an empty editor, ready to paste a script into ── */
  $('pp-own').addEventListener('click', () => {
    editingId = null;
    fillEditor({ name: '', text: '## Opening\n', key_points: [], hard_questions: [], objections: [], cheat_sheet: [], minutes: Number($('pp-minutes').value), client: $('pp-client').value });
    $('pp-script').focus();
    $('pp-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  /* ── Save ── */
  $('pp-save').addEventListener('click', async () => {
    try {
      const body = readEditor();
      const saved = editingId
        ? await api(`/api/scripts/${editingId}`, { method: 'PUT', json: body })
        : await api('/api/scripts', { method: 'POST', json: body });
      editingId = saved.script_id;
      try { localStorage.setItem('pitchmirror_last_script', saved.script_id); } catch (_) { /* ignore */ }
      $('pp-saved').classList.remove('hidden');
      loadScripts();
    } catch (e) { alert(e.message); }
  });

  /* ── My scripts ── */
  async function loadScripts() {
    try {
      const { scripts } = await api('/api/scripts');
      $('pp-scripts').innerHTML = scripts.map((s) => `<li><b>${esc(s.name)}</b><span class="sub">${s.lines} lines</span>
        <button class="open" data-open="${esc(s.script_id)}">Edit</button><button data-del="${esc(s.script_id)}">Delete</button></li>`).join('');
      $('pp-scripts').querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', async () => {
        const s = await api(`/api/scripts/${b.dataset.open}`);
        editingId = s.script_id;
        fillEditor(s);
        $('pp-result').scrollIntoView({ behavior: 'smooth' });
      }));
      $('pp-scripts').querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
        if (!confirm('Delete this script?')) return;
        await api(`/api/scripts/${b.dataset.del}`, { method: 'DELETE' });
        if (editingId === b.dataset.del) { editingId = null; $('pp-result').classList.add('hidden'); }
        loadScripts();
      }));
    } catch (_) { /* signed out handled by tester.js */ }
  }

  /* ── My documents ── */
  async function loadDocs() {
    try {
      const { docs } = await api('/api/library');
      $('pp-docs').innerHTML = docs.map((d) => `<li><b>${esc(d.title)}</b><span class="sub">${Math.max(1, Math.round(d.size / 1000))}k chars</span>
        <button data-doc="${esc(d.doc_id)}">Delete</button></li>`).join('');
      $('pp-docs').querySelectorAll('[data-doc]').forEach((b) => b.addEventListener('click', async () => {
        await api(`/api/library/${b.dataset.doc}`, { method: 'DELETE' });
        loadDocs();
      }));
      $('pp-lib-hint').textContent = docs.length ? `(${docs.length} saved)` : '(none saved yet)';
      $('pp-use-lib').disabled = !docs.length;
    } catch (_) { /* ignore */ }
  }

  $('pp-file').addEventListener('change', async () => {
    const file = $('pp-file').files[0];
    if (!file) return;
    say($('pp-doc-status'), `Adding ${file.name}…`);
    const form = new FormData();
    form.append('file', file);
    try {
      await api('/api/library', { method: 'POST', body: form });
      say($('pp-doc-status'), `Added ${file.name}.`);
      loadDocs();
    } catch (e) { say($('pp-doc-status'), e.message, true); }
    $('pp-file').value = '';
  });

  $('pp-doc-add').addEventListener('click', async () => {
    try {
      await api('/api/library', { method: 'POST', json: { title: $('pp-doc-title').value, text: $('pp-doc-text').value } });
      $('pp-doc-title').value = ''; $('pp-doc-text').value = '';
      say($('pp-doc-status'), 'Added.');
      loadDocs();
    } catch (e) { say($('pp-doc-status'), e.message, true); }
  });

  loadScripts();
  loadDocs();
})();
