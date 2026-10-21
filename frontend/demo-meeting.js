/**
 * Pitchlight — practice meeting (test mode) for Meeting Mode.
 *
 * A built-in dummy client (Priya) speaks pre-recorded questions. The audio is played through the speakers AND fed into
 * the same "other side" audio path a real tab-audio share uses, so the server transcribes it, answers it and coaches
 * exactly as in a real call. While Priya speaks the mic is not sent (no echo, no headphones needed). A guide walks
 * through 11 steps and ticks each feature when the server actually produces it.
 * Talks to meeting.js through window.PMBridge.
 */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const RATE = 16000, CHUNK = 1600, TAIL_TICKS = 7;      // 100 ms chunks; 0.7 s of mic muting after Priya stops

  let kit = null, clips = {}, bridge = null;
  let ctx = null;                       // playback context, created inside the Start click
  let idx = 0, sat = {}, skipped = {}, played = {};
  let sections = null, status = null, lostCard = null;
  let play = null;                      // { i, clip, pos, ended, tail }
  let finished = false;
  let latestSay = null;                 // the newest ready-to-read answer { question, say, prepared }

  const headers = () => {
    const h = { 'x-user-id': localStorage.getItem('pitchmirror_user_id') || '' };
    const t = (localStorage.getItem('pitchmirror_api_token') || '').trim();
    if (t) h.Authorization = `Bearer ${t}`;
    return h;
  };

  /* ── Set-up card ── */
  async function init() {
    bridge = window.PMBridge;
    if (!bridge || !$('mt-practice')) return;
    try {
      const resp = await fetch('/api/demo-kit', { headers: headers() });
      if (!resp.ok) return;
      kit = await resp.json();
    } catch (_) { return; }
    $('mt-practice').classList.remove('hidden');
    $('mt-practice').classList.toggle('is-new', !kit.completed);
    $('mt-practice-badge').classList.toggle('hidden', !kit.completed);
    $('mt-practice-btn').addEventListener('click', begin);
    preload();
  }

  async function preload() {
    const off = new OfflineAudioContext(1, 1, RATE);
    await Promise.all(kit.steps.map(async (s) => {
      try {
        const r = await fetch(`${kit.audio_base}${s.id}.m4a`);
        const buf = await off.decodeAudioData(await r.arrayBuffer());
        clips[s.id] = buf.getChannelData(0).slice();
      } catch (e) { console.warn('practice audio failed', s.id, e); }
    }));
  }

  function begin() {
    if (!kit) return;
    if (Object.keys(clips).length < kit.steps.length) {
      $('mt-practice-btn').textContent = 'Loading the client’s voice…';
      setTimeout(() => $('mt-practice-btn').textContent = 'Start practice meeting', 2500);
      return;
    }
    const set = (id, v) => { const el = $(id); if (el) el.value = v; };
    set('mt-mode', kit.mode); set('mt-persona', kit.persona); set('mt-chatty', kit.chatty); set('mt-platform', 'other');
    set('mt-client', kit.client); set('mt-notes', kit.notes); set('mt-points', kit.points.join('\n'));
    set('mt-objections', kit.objections); set('mt-minutes', String(kit.minutes));
    set('mt-script', ''); set('mt-business', '');
    if ($('mt-use-library')) $('mt-use-library').checked = false;
    if ($('mt-camera')) $('mt-camera').checked = false;
    bridge.onStart = onStart;
    bridge.start();                      // synchronous up to the first await: keeps the click's permission to open the notch
  }

  /* ── During the meeting ── */
  function onStart(practice) {
    if (!practice) return;
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    ctx.resume().catch(() => {});
    idx = 0; sat = {}; skipped = {}; played = {}; sections = null; status = null; lostCard = null; play = null; finished = false; latestSay = null;
    bridge.onTick = tick;
    bridge.onMessage = onMessage;
    $('mt-guide').classList.remove('hidden');
    render();
  }

  function startClip(i) {
    if (play || !ctx) return;
    const step = kit.steps[i];
    const clip = clips[step.id];
    if (!clip) return;
    ctx.resume().catch(() => {});
    const buf = ctx.createBuffer(1, clip.length, RATE);
    buf.copyToChannel(clip, 0);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    play = { i, clip, pos: 0, ended: false, tail: 0 };
    src.onended = () => { if (play) play.ended = true; };
    bridge.micGate = true;
    played[step.id] = true;
    lostCard = null;
    src.start();
    render();
  }

  /** Called for every 100 ms of microphone audio: send 100 ms of the client's side (speech or silence). */
  function tick() {
    if (!kit) return;
    const chunk = new Float32Array(CHUNK);
    if (play && play.pos < play.clip.length) {
      chunk.set(play.clip.subarray(play.pos, play.pos + CHUNK));
      play.pos += CHUNK;
    } else if (play && play.ended) {
      if (++play.tail >= TAIL_TICKS) { play = null; bridge.micGate = false; render(); }
    }
    bridge.sendOther(chunk);
  }

  /* ── What the server produced → feature ticks ── */
  function matches(step, msg) {
    const q = String(msg.question || '').toLowerCase();
    const src = (msg.sources || []).join(' ').toLowerCase();
    switch (step.expect) {
      case 'script': return msg.type === 'script_pos' && (msg.status.covered || []).length >= 4;
      case 'mood': return msg.type === 'client_mood' || msg.type === 'advice';
      case 'answer_company': return msg.type === 'answer_hint' && /cost|price|fee/.test(q) && src.startsWith('company:');
      case 'answer_clinic': return msg.type === 'answer_hint' && src.includes('clinic suite');
      case 'objection': return msg.type === 'objection_card';
      case 'not_in_notes': return msg.type === 'answer_hint' && !!msg.not_in_notes;
      case 'lost': return msg.type === 'script_where';
      case 'talk': return msg.type === 'coach_tip' && msg.issue_type === 'monologue';
      case 'answer_data': return msg.type === 'answer_hint' && /data/.test(q) && !!src;
      case 'commit': return msg.type === 'commitments' && (msg.items || []).length > 0;
      case 'finish': return msg.type === 'scorecard';
      default: return false;
    }
  }

  function onMessage(msg) {
    let changed = false;
    if (msg.type === 'script_loaded' && msg.sections) { sections = msg.sections; status = msg.status; changed = true; }
    if ((msg.type === 'script_pos' || msg.type === 'script_where') && msg.status) { status = msg.status; changed = true; }
    if (msg.type === 'answer_hint' && msg.say) { latestSay = { question: msg.question, say: msg.say, prepared: !!msg.prepared }; changed = true; }
    if (msg.type === 'script_where') lostCard = msg;
    if (msg.type === 'script_pos') lostCard = null;
    for (const s of kit.steps) {
      if (!sat[s.id] && matches(s, msg)) { sat[s.id] = true; changed = true; }
    }
    if (msg.type === 'scorecard') { finished = true; bridge.micGate = false; play = null; changed = true; }
    if (changed) render();
  }

  /* ── Guide ── */
  function go(delta) { idx = Math.max(0, Math.min(kit.steps.length - 1, idx + delta)); lostCard = null; latestSay = null; render(); }

  function render() {
    const box = $('mt-guide');
    if (!box || !kit || box.classList.contains('hidden')) return;
    const total = kit.steps.length;
    const doneCount = kit.steps.filter((s) => sat[s.id]).length;
    if (finished) { box.innerHTML = summary(doneCount, total); bindSummary(box); return; }
    const step = kit.steps[idx];
    const speaking = !!play;
    const phase = speaking ? 'priya' : (played[step.id] ? 'you' : 'ready');
    const seen = !!sat[step.id];
    const last = idx === total - 1;
    box.innerHTML = `
      <div class="mg-head">
        <div><b>Practice meeting</b> <span class="mg-sub">Step ${idx + 1} of ${total}: ${esc(step.feature)}</span></div>
        <div class="mg-count">${doneCount} of ${total} features seen</div>
      </div>
      <div class="mg-bar"><i style="width:${Math.round(100 * doneCount / total)}%"></i></div>
      <div class="mg-chips">${kit.steps.map((s, i) => `<button class="mg-chip${i === idx ? ' is-cur' : ''}${sat[s.id] ? ' is-ok' : ''}${skipped[s.id] ? ' is-skip' : ''}" data-i="${i}" title="${esc(s.feature)}">${sat[s.id] ? '✓' : i + 1}</button>`).join('')}</div>
      <div class="mg-grid">
        <div class="mg-card">
          <div class="mg-who">Priya, Head of Operations at Northwind Dental</div>
          <div class="mg-line">“${esc(step.client)}”</div>
          <div class="mg-actions">
            <button class="mt-btn mt-btn-primary mg-play" ${speaking ? 'disabled' : ''}>${speaking ? '🔊 Priya is speaking…' : played[step.id] ? '↻ Ask again' : '▶ Priya asks'}</button>
            <span class="mg-phase">${phase === 'ready' ? 'Press the button when you are ready.' : phase === 'priya' ? 'Listen. Your mic is paused while she speaks.' : 'Your turn. Speak now.'}</span>
          </div>
          ${latestSay && (played[step.id] || sat[step.id]) ? `<div class="mg-say"><b>${latestSay.prepared ? 'Your prepared answer' : 'Say this'}</b>${esc(latestSay.say)}</div>` : ''}
          <div class="mg-you"><b>You:</b> ${esc(step.you)}</div>
          <div class="mg-watch ${seen ? 'is-seen' : ''}"><span class="mg-tick">${seen ? '✓ Seen' : '○ Waiting'}</span> ${esc(step.watch)}</div>
          <div class="mg-nav">
            <button class="mt-btn mt-btn-ghost mg-prev" ${idx === 0 ? 'disabled' : ''}>← Back</button>
            ${last ? '<span class="mg-hint">After the last answer press <b>End meeting</b>.</span>'
                   : `<button class="mt-btn ${seen ? 'mt-btn-primary' : 'mt-btn-ghost'} mg-next">${seen ? 'Next step →' : 'Skip this step →'}</button>`}
          </div>
        </div>
        <div class="mg-card mg-script">
          <div class="mg-who">Your script <span class="mg-sub">${status ? status.pct + '% covered' : ''}</span>
            <button class="mg-lost">🧭 I'm lost</button></div>
          ${lostCard ? lostHtml(lostCard) : ''}
          <div class="mg-script-body">${sections ? window.ScriptView.render(sections, status || { next: 0, covered: [], skipped: [] }, { clickable: true }) : '<p class="mg-sub">The script appears when the meeting connects.</p>'}</div>
        </div>
      </div>`;
    box.querySelector('.mg-play')?.addEventListener('click', () => startClip(idx));
    box.querySelector('.mg-next')?.addEventListener('click', () => { if (!sat[step.id]) skipped[step.id] = true; go(1); });
    box.querySelector('.mg-prev')?.addEventListener('click', () => go(-1));
    box.querySelectorAll('.mg-chip').forEach((c) => c.addEventListener('click', () => { idx = Number(c.dataset.i); lostCard = null; latestSay = null; render(); }));
    box.querySelector('.mg-lost')?.addEventListener('click', () => bridge.askLost());
    box.querySelectorAll('.sv-line').forEach((li) => li.addEventListener('click', () => bridge.gotoLine(Number(li.dataset.idx))));
    window.ScriptView.scrollToNext(box.querySelector('.mg-script-body'));
  }

  function lostHtml(m) {
    const next = (m.next || []).length ? `<div class="mg-lost-next">Say next: ${esc(m.next[0])}</div>${m.next[1] ? `<div>Then: ${esc(m.next[1])}</div>` : ''}` : '<div class="mg-lost-next">That was the last line.</div>';
    return `<div class="mg-lost-card">${m.here ? `<div>You just said: “${esc(m.here)}”</div>` : ''}${next}</div>`;
  }

  function summary(n, total) {
    return `<div class="mg-head"><div><b>Practice complete</b> <span class="mg-sub">${n} of ${total} features seen</span></div></div>
      <div class="mg-bar"><i style="width:${Math.round(100 * n / total)}%"></i></div>
      <ul class="mg-sum">${kit.steps.map((s) => `<li class="${sat[s.id] ? 'ok' : 'no'}">${sat[s.id] ? '✓' : '○'} ${esc(s.feature)}</li>`).join('')}</ul>
      <p class="mg-hint">${n >= total - 2 ? 'You are ready for a real client meeting. Check the review below: recap, follow-up email, question review and readiness.'
        : 'A few features did not appear. That is normal: the coach is selective, and some steps depend on what you said. Run the practice again, or ask your admin if something never shows up.'}</p>
      <div class="mg-actions"><button class="mt-btn mt-btn-primary mg-again">Run the practice again</button>
        <a class="mt-btn mt-btn-ghost" href="/app?view=history">Open History</a></div>`;
  }
  function bindSummary(box) { box.querySelector('.mg-again')?.addEventListener('click', () => location.reload()); }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(init, 0));
  else setTimeout(init, 0);
})();
