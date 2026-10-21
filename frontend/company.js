/** Pitchlight — company hub: businesses and their documents. Everyone reads; admins add and remove. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const headers = (json) => {
    const h = { 'x-user-id': localStorage.getItem('pitchmirror_user_id') || '' };
    const token = (localStorage.getItem('pitchmirror_api_token') || '').trim();
    if (token) h.Authorization = `Bearer ${token}`;
    if (json) h['Content-Type'] = 'application/json';
    return h;
  };
  async function api(path, opts = {}) {
    const resp = await fetch(path, { method: opts.method || 'GET', headers: headers(!!opts.json), body: opts.json ? JSON.stringify(opts.json) : opts.body });
    let data = null;
    try { data = await resp.json(); } catch (_) { /* empty */ }
    if (!resp.ok) throw new Error((data && data.detail) || `Something went wrong (${resp.status})`);
    return data;
  }
  const say = (text, err) => { $('cp-status').textContent = text || ''; $('cp-status').classList.toggle('err', !!err); };

  async function load() {
    let data;
    try { data = await api('/api/company'); } catch (_) { return; }
    const edit = data.can_edit;
    $('cp-role').textContent = edit ? ' You are an admin: you can add businesses and documents.' : ' Only admins can change it.';
    $('cp-add-business').classList.toggle('hidden', !edit);
    const ventures = data.ventures || [];
    $('cp-list').innerHTML = ventures.length ? ventures.map((v) => `
      <section class="mt-card pp-card cp-venture" data-venture="${esc(v.name)}">
        <div class="cp-head">
          <div class="cp-sum"><h2 class="pp-h2">${esc(v.name)}</h2>
            ${v.description ? `<p class="pp-hint">${esc(v.description)}</p>` : (edit && v.name !== 'General' ? '<p class="pp-hint">No summary yet. A short paragraph helps the coach answer "what does this business do?".</p>' : '')}
            ${edit && v.name !== 'General' ? `<button class="cp-link cp-edit" data-name="${esc(v.name)}" data-desc="${esc(v.description)}">${v.description ? 'Edit summary' : 'Add summary'}</button>` : ''}</div>
          ${edit && v.name !== 'General' ? `<button class="cp-del-venture" data-name="${esc(v.name)}">Remove business</button>` : ''}
        </div>
        <ul class="pp-list">${v.docs.map((d) => `<li><b>${esc(d.title)}</b>
          <span class="sub">${Math.max(1, Math.round(d.size / 1000))}k chars</span>
          ${edit ? `<button data-doc="${esc(d.doc_id)}">Delete</button>` : ''}</li>`).join('')}</ul>
        ${edit ? `<div class="cp-upload">
          <label class="mt-btn mt-btn-ghost cp-file">Upload decks or documents
            <input type="file" multiple hidden data-venture="${esc(v.name === 'General' ? '' : v.name)}" accept=".pdf,.pptx,.docx,.txt,.md,.markdown,.csv" /></label>
          <details><summary>or paste text</summary>
            <input type="text" class="cp-paste-title" maxlength="80" placeholder="Title, e.g. Pricing 2026" />
            <textarea class="cp-paste-text" rows="5" placeholder="Paste the text"></textarea>
            <button class="mt-btn mt-btn-ghost cp-paste-add" data-venture="${esc(v.name === 'General' ? '' : v.name)}">Add</button></details>
        </div>` : ''}
      </section>`).join('')
      : `<section class="mt-card pp-card"><p class="pp-hint">${edit ? 'No businesses yet. Add the first one above, then upload its decks.' : 'The admin has not added anything yet.'}</p></section>`;

    document.querySelectorAll('.cp-edit').forEach((b) => b.addEventListener('click', () => {
      const box = b.closest('.cp-sum');
      box.innerHTML = `<textarea rows="4" maxlength="1500">${esc(b.dataset.desc)}</textarea>
        <div><button class="mt-btn mt-btn-primary cp-save">Save summary</button> <button class="cp-link cp-cancel">Cancel</button></div>`;
      box.querySelector('.cp-cancel').addEventListener('click', load);
      box.querySelector('.cp-save').addEventListener('click', async () => {
        try { await api(`/api/company/ventures?name=${encodeURIComponent(b.dataset.name)}`, { method: 'PUT', json: { description: box.querySelector('textarea').value } }); say('Summary saved.'); }
        catch (e) { say(e.message, true); }
        load();
      });
    }));
    document.querySelectorAll('[data-doc]').forEach((b) => b.addEventListener('click', async () => {
      if (!confirm('Remove this document from the hub?')) return;
      try { await api(`/api/company/${b.dataset.doc}`, { method: 'DELETE' }); } catch (e) { say(e.message, true); }
      load();
    }));
    document.querySelectorAll('.cp-del-venture').forEach((b) => b.addEventListener('click', async () => {
      if (!confirm(`Remove "${b.dataset.name}" and ALL of its documents?`)) return;
      try { const r = await api(`/api/company/ventures?name=${encodeURIComponent(b.dataset.name)}`, { method: 'DELETE' }); say(`Removed ${b.dataset.name} (${r.documents_removed} documents).`); }
      catch (e) { say(e.message, true); }
      load();
    }));
    document.querySelectorAll('.cp-file input').forEach((inp) => inp.addEventListener('change', async () => {
      const venture = inp.dataset.venture;
      for (const file of [...inp.files]) {
        say(`Adding ${file.name}…`);
        const form = new FormData();
        form.append('file', file);
        try { await api(`/api/company?venture=${encodeURIComponent(venture)}`, { method: 'POST', body: form }); say(`Added ${file.name}.`); }
        catch (e) { say(`${file.name}: ${e.message}`, true); break; }
      }
      load();
    }));
    document.querySelectorAll('.cp-paste-add').forEach((b) => b.addEventListener('click', async () => {
      const box = b.closest('details');
      try {
        await api('/api/company', { method: 'POST', json: { title: box.querySelector('.cp-paste-title').value, text: box.querySelector('.cp-paste-text').value, venture: b.dataset.venture } });
        say('Added.');
        load();
      } catch (e) { say(e.message, true); }
    }));
  }

  $('cp-add').addEventListener('click', async () => {
    const btn = $('cp-add');
    const name = $('cp-name').value.trim();
    btn.disabled = true;
    try {
      await api('/api/company/ventures', { method: 'POST', json: { name, description: $('cp-desc').value } });
      const files = [...$('cp-files').files];
      for (const file of files) {
        say(`Adding ${name}: ${file.name}…`);
        const form = new FormData();
        form.append('file', file);
        try { await api(`/api/company?venture=${encodeURIComponent(name)}`, { method: 'POST', body: form }); }
        catch (e) { say(`${name} added, but ${file.name} failed: ${e.message}`, true); files.length = 0; break; }
      }
      if (files.length || !$('cp-files').files.length) say(`${name} added${$('cp-files').files.length ? ` with ${$('cp-files').files.length} document(s)` : ''}.`);
      $('cp-name').value = ''; $('cp-desc').value = ''; $('cp-files').value = '';
    } catch (e) { say(e.message, true); }
    btn.disabled = false;
    load();
  });
  load();
})();
