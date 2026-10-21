/**
 * Pitchlight — Meeting Mode client.
 *
 * Runs the coach next to a real call (Zoom, Google Meet, Teams, …):
 * - streams the microphone; the server transcribes it with a dedicated live speech-to-text
 *   model and measures pace / energy / tone from the raw audio
 * - optionally captures the meeting tab / system audio so the coach also hears the other
 *   participant (transcribed the same way, shown as "Them")
 * - optional webcam for in-browser eye-contact tracking (only gaze labels are sent)
 * - shows silent text nudges in a floating "bubble"; when the browser supports
 *   Document Picture-in-Picture the bubble is an always-on-top mini window that can
 *   be dragged anywhere on screen, otherwise it is a draggable widget on this page
 * - never plays coach audio (the server doesn't send any in meeting mode)
 * - when the meeting ends, the session is analysed and saved like any practice run,
 *   so it shows up in the app's History with transcript + review.
 */
(function () {
  'use strict';

  /* ── Config (frame types must match backend/main.py) ── */
  const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
  const AUDIO_SAMPLE_RATE = 16000;
  const AUDIO_CHUNK_SAMPLES = 1600;         // 100 ms
  const VIDEO_W = 480, VIDEO_H = 360;
  const NUDGE_HOLD_MS = 9000;               // how long a nudge stays highlighted
  const T_AUDIO_IN = 0x01, T_STOP = 0x03, T_OTHER_AUDIO_IN = 0x06;
  const USER_ID_KEY = 'pitchmirror_user_id';
  const BUBBLE_POS_KEY = 'pitchmirror_bubble_pos';
  const SETTINGS_KEY = 'pitchmirror_meeting_settings';
  const SIZE_KEY = 'pitchmirror_bubble_size';
  const PREFS_KEY = 'pitchmirror_bubble_prefs';
  // Holding the floor this long gets a "check in" nudge (none for presentations: one long turn by design).
  const MONOLOGUE_LIMIT_S = { sales_demo: 90, general: 90, pitch: 120, interview: 120 };
  const TALK_SHARE_MODES = ['sales_demo', 'general'];   // two-way conversations only
  const TALK_SHARE_WINDOW_MS = 300000;  // talk share over the last 5 minutes…
  const TALK_SHARE_MIN_S = 90;          // …once at least 90 s of talking happened in it
  const TALK_SHARE_LIMIT = 0.75;
  const TALK_SHARE_GAP_MS = 240000;     // at most one talk-share report per 4 minutes
  const TALK_STATS_EVERY_MS = 15000;
  const VOICE_CHUNK_S = 0.1;
  const IDLE_DIM_MS = 15000;          // dim the bubble after 15 s with nothing new
  const YOU_VOICE_RMS = 0.03;         // mic level that counts as you speaking (headphones assumed)
  const THEM_VOICE_RMS = 0.012;       // meeting-audio level that counts as them speaking
  const THEM_HOLD_MS = 1200;          // "client is talking" if they were heard within this window
  const INTERRUPT_OVERLAP_MS = 800;   // you talking over them for this long = an interruption
  const PIP_WIDTH = 410;
  const HINT_HOLD_MS = 25000;         // an answer outline stays open this long
  const RECENT_MAX = 3;
  const FILLER_RE = /\b(um+|uh+|erm+|uhm+|you know|basically|literally|kind of|sort of|i mean)\b/gi;
  const ISSUE_LABELS = {
    filler: 'Filler words', pace: 'Pace', eye_contact: 'Eye contact',
    contradiction: 'Contradiction', clarity: 'Clarity', off_topic: 'Off topic',
    rambling: 'Rambling', monotone: 'Flat tone', confidence: 'Hedging', interruption: 'Interrupted',
    monologue: 'Long monologue', talk_share: 'Talk time',
  };
  const DEFAULT_CUES = {
    filler: 'Pause instead of fillers.',
    pace: 'Slow down — breathe.',
    eye_contact: 'Look at the camera.',
    contradiction: 'Keep your story consistent.',
    clarity: 'Say it in one short sentence.',
    off_topic: 'Answer the question they asked.',
    rambling: 'Wrap up — make your point.',
    monotone: 'Add energy — vary your tone.',
    confidence: 'Drop the hedging — be direct.',
    interruption: 'Let them finish.',
    monologue: 'Long answer — pause and ask them a question.',
    talk_share: "You're doing most of the talking — ask and listen.",
  };
  const GAZE_TEXT = { camera: 'eyes ✓', away: 'looking away', notes: 'looking down', no_face: 'out of frame' };

  const USER_ID = (() => {
    const existing = localStorage.getItem(USER_ID_KEY);
    if (existing && /^[A-Za-z0-9_.-]{3,64}$/.test(existing)) return existing;
    const id = `user-${(crypto.randomUUID?.() || Math.random().toString(36).slice(2)).slice(0, 12)}`;
    localStorage.setItem(USER_ID_KEY, id);
    return id;
  })();

  /* ── DOM ─────────────────────────────────────────── */
  const $ = (id) => document.getElementById(id);
  const setupEl = $('mt-setup');
  const liveEl = $('mt-live');
  const startBtn = $('mt-start');
  const endBtn = $('mt-end');
  const errorEl = $('mt-error');
  const statusEl = $('mt-live-status');
  const timerEl = $('mt-live-timer');
  const pipNoteEl = $('mt-pip-note');
  const reopenBtn = $('mt-reopen');
  const transcriptEl = $('mt-transcript');
  const nudgesEl = $('mt-nudges');
  const resultEl = $('mt-result');
  const videoEl = $('mt-video');
  const fields = {
    mode: $('mt-mode'), platform: $('mt-platform'), persona: $('mt-persona'), camera: $('mt-camera'),
  };
  const briefFields = { notes: $('mt-notes'), points: $('mt-points'), minutes: $('mt-minutes') };
  const pointsPanel = $('mt-points-panel');
  const pointsList = $('mt-points-list');
  const pointsCount = $('mt-points-count');
  const otherBtn = $('mt-other-btn');
  const otherStatusEl = $('mt-other-status');
  const clientField = $('mt-client');
  const clientsList = $('mt-clients');
  const lastCallEl = $('mt-last-call');
  const commitPanel = $('mt-commit-panel');
  const commitList = $('mt-commit-list');
  const commitCount = $('mt-commit-count');
  const rehearseBtn = $('mt-rehearse-btn');
  const rehearseBody = $('mt-rehearse-body');

  /* ── State ───────────────────────────────────────── */
  let ws = null;
  let stream = null;
  let audioCtx = null;
  let worklet = null;
  let active = false;        // live phase running
  let paused = false;
  let ending = false;
  let sessionId = null;
  let startedAt = 0;
  let timerHandle = null;
  let pipWindow = null;
  let otherStream = null;    // meeting tab / screen capture carrying the other side's audio
  let otherCtx = null;
  let otherWorklet = null;
  let bubble = null;         // the bubble root element (wherever it lives)
  let nudgeTimer = null;
  let activeLine = null;     // { who: 'you' | 'them', el }
  let wordLog = [];          // [{at, words}] for the rolling WPM display
  let fillerTotal = 0;
  let youWords = 0;
  let themWords = 0;
  // Live audio activity (from the PCM chunks) for meters, smart quiet, answer timer, interruptions.
  let youLevel = 0, themLevel = 0;
  let lastYouVoice = 0, lastThemVoice = 0, themTalkStart = 0, youTalkStart = 0;
  let overlapStart = 0, lastInterruptAt = 0;
  let answerStart = 0, answerWrapShown = false;
  let heldNudge = null;               // tip held back while the client is talking
  let currentNudgeIndex = null;       // server event index of the tip on screen (for 👍/👎)
  let lastActivityAt = Date.now();
  let prefs = { opacity: 100, scale: 'm', dim: true, quiet: true };
  try { prefs = { ...prefs, ...JSON.parse(localStorage.getItem(PREFS_KEY) || '{}') }; } catch (_) { /* ignore */ }
  let gazeState = null;
  const eventCards = new Map();
  // Talk time from voice activity (100 ms chunks): totals, recent windows, longest turn.
  let youVoicedS = 0, themVoicedS = 0;
  let youVoiceTimes = [], themVoiceTimes = [];
  let longestMonologueS = 0, lastShareReportAt = 0, lastStatsSentAt = 0;
  let commitments = [];

  /* ── Settings persistence ───────────────────────── */
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
    for (const k of ['mode', 'platform', 'persona']) if (saved[k]) fields[k].value = saved[k];
    if (typeof saved.camera === 'boolean') fields.camera.checked = saved.camera;
    if (saved.client && clientField) clientField.value = saved.client;
  } catch (_) { /* ignore */ }
  function saveSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({
        mode: fields.mode.value, platform: fields.platform.value,
        persona: fields.persona.value, camera: fields.camera.checked,
        client: (clientField?.value || '').trim(),
      }));
    } catch (_) { /* ignore */ }
  }

  if (window.GazeTracker && fields.camera.checked) window.GazeTracker.preload();

  /* ── Helpers ─────────────────────────────────────── */
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (secs) => { const s = Math.max(0, Math.floor(secs)); return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; };
  const elapsed = () => (startedAt ? (Date.now() - startedAt) / 1000 : 0);

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.toggle('hidden', !msg);
  }

  /* ── Bubble ──────────────────────────────────────── */
  function createBubble() {
    const frag = $('mt-bubble-template').content.cloneNode(true);
    const el = frag.querySelector('.pm-bubble');
    el.querySelector('.pm-pause').addEventListener('click', togglePause);
    el.querySelector('.pm-end').addEventListener('click', endMeeting);
    el.querySelector('.pm-size').addEventListener('click', toggleSize);
    el.querySelector('.pm-settings-btn').addEventListener('click', () => {
      const panel = el.querySelector('.pm-settings');
      panel.classList.toggle('hidden');
      el.querySelector('.pm-settings-btn').setAttribute('aria-expanded', String(!panel.classList.contains('hidden')));
      if (!panel.classList.contains('hidden')) el.dataset.open = 'true';
      fitPipWindow();
    });
    const detailsBtn = el.querySelector('.pm-details-btn');
    detailsBtn.addEventListener('click', () => {
      markActivity();
      if (el.dataset.size === 'compact') { toggleSize(); return; }   // pill-only → show the card again
      const on = el.dataset.details !== 'true';
      el.dataset.details = on ? 'true' : 'false';
      detailsBtn.setAttribute('aria-expanded', String(on));
      detailsBtn.title = on ? 'Hide details' : 'Show details';
      fitPipWindow();
    });
    // Tap the pill (not a drag) to peek at the card.
    el.querySelector('.pm-pill').addEventListener('click', (e) => {
      if (e.target.closest('button') || el.dataset.dragged === '1') return;
      markActivity();
      el.dataset.open = el.dataset.open === 'true' ? 'false' : 'true';
      fitPipWindow();
    });
    const opacity = el.querySelector('.pm-opt-opacity');
    const scale = el.querySelector('.pm-opt-scale');
    const dim = el.querySelector('.pm-opt-dim');
    const quiet = el.querySelector('.pm-opt-quiet');
    opacity.value = prefs.opacity; scale.value = prefs.scale; dim.checked = prefs.dim; quiet.checked = prefs.quiet;
    opacity.addEventListener('input', () => { prefs.opacity = Number(opacity.value); applyPrefs(el); savePrefs(); });
    scale.addEventListener('change', () => { prefs.scale = scale.value; applyPrefs(el); savePrefs(); fitPipWindow(); });
    dim.addEventListener('change', () => { prefs.dim = dim.checked; savePrefs(); markActivity(); });
    quiet.addEventListener('change', () => { prefs.quiet = quiet.checked; savePrefs(); });
    applyPrefs(el);
    el.querySelector('.pm-ring').classList.add('hidden');
    el.querySelector('.pm-up').addEventListener('click', () => rateNudge(true));
    el.querySelector('.pm-down').addEventListener('click', () => rateNudge(false));
    el.querySelectorAll('.pm-help-btn').forEach((btn) => btn.addEventListener('click', () => askHelp(btn.dataset.help, btn)));
    el.addEventListener('pointerenter', markActivity);
    try { if (localStorage.getItem(SIZE_KEY) === 'compact') el.dataset.size = 'compact'; } catch (_) { /* ignore */ }
    syncSizeButton(el);
    return el;
  }

  function b(sel) { return bubble ? bubble.querySelector(sel) : null; }

  function setBubbleState(state, label) {
    if (!bubble) return;
    bubble.dataset.state = state;            // idle | listening | nudge | alert | paused | analyzing | done
    const st = b('.pm-state');
    if (st && label) st.textContent = label;
  }

  function applyPrefs(el = bubble) {
    if (!el) return;
    el.style.setProperty('--pm-opacity', String(Math.max(40, Math.min(100, prefs.opacity)) / 100));
    el.dataset.scale = ['s', 'm', 'l'].includes(prefs.scale) ? prefs.scale : 'm';
  }

  function savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) { /* ignore */ }
  }

  function markActivity() {
    lastActivityAt = Date.now();
    bubble?.classList.remove('is-idle');
  }

  const themSpeaking = () => Date.now() - lastThemVoice < THEM_HOLD_MS;

  function rateNudge(useful) {
    if (currentNudgeIndex == null || !ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: 'nudge_feedback', index: currentNudgeIndex, useful }));
    const box = b('.pm-rate');
    box.classList.add('voted');
    box.textContent = useful ? 'Thanks 👍' : 'Noted 👎';
    currentNudgeIndex = null;
  }

  function resetRateButtons(show) {
    const box = b('.pm-rate');
    if (!box) return;
    if (box.classList.contains('voted')) {
      box.classList.remove('voted');
      box.innerHTML = `<button type="button" class="pm-rate-btn pm-up" title="Helpful" aria-label="This tip was helpful">👍</button>
        <button type="button" class="pm-rate-btn pm-down" title="Not helpful" aria-label="This tip was not helpful">👎</button>`;
      box.querySelector('.pm-up').addEventListener('click', () => rateNudge(true));
      box.querySelector('.pm-down').addEventListener('click', () => rateNudge(false));
    }
    box.classList.toggle('hidden', !show);
  }

  let lastNudgeText = '';
  /**
   * Show a nudge. opts.index: server event index (enables 👍/👎); opts.urgent: never hold.
   * Coaching tips are held while the client is talking (smart quiet) and shown when they pause.
   */
  function showNudge(text, level = 'nudge', opts = {}) {
    if (!bubble || !text) return;
    if (prefs.quiet && !opts.urgent && ['nudge', 'alert'].includes(level) && themSpeaking()) {
      heldNudge = { text, level, opts };
      b('.pm-state').textContent = 'Tip ready…';
      return;
    }
    markActivity();
    currentNudgeIndex = Number.isInteger(opts.index) ? opts.index : null;
    resetRateButtons(currentNudgeIndex != null);
    if (lastNudgeText && lastNudgeText !== text && ['nudge', 'alert'].includes(level)) pushRecent(lastNudgeText);
    if (['nudge', 'alert'].includes(level)) lastNudgeText = text;
    b('.pm-nudge').textContent = text;
    b('.pm-mini').textContent = text;
    setBubbleState(level, level === 'alert' ? 'Heads up' : 'Nudge');
    if (nudgeTimer) clearTimeout(nudgeTimer);
    openDrop(true);
    nudgeTimer = setTimeout(() => {
      if (!active) return;
      settleToListening();
    }, NUDGE_HOLD_MS);
  }

  /** Fold the notch back to its resting pill (unless an outline or the settings are showing). */
  function settleToListening() {
    if (!bubble) return;
    setBubbleState(paused ? 'paused' : 'listening', paused ? 'Paused' : 'Listening');
    const hintOpen = !b('.pm-hint')?.classList.contains('hidden');
    const settingsOpen = !b('.pm-settings')?.classList.contains('hidden');
    if (!hintOpen) b('.pm-mini').textContent = restingLabel();
    if (!hintOpen && !settingsOpen) openDrop(false);
  }

  function restingLabel() {
    if (paused) return 'Paused — not listening';
    const total = briefPoints.length;
    const done = briefPoints.filter((p) => p.covered).length;
    return total ? `Listening · ${done}/${total} points` : 'Listening';
  }

  function openDrop(open) {
    if (bubble) bubble.dataset.open = open ? 'true' : 'false';
  }

  let hintTimer = null;
  function scheduleHintClose() {
    if (hintTimer) clearTimeout(hintTimer);
    hintTimer = setTimeout(() => {
      b('.pm-hint')?.classList.add('hidden');
      bubble?.classList.remove('has-hint');
      if (active) settleToListening();
    }, HINT_HOLD_MS);
  }

  function pushRecent(text) {
    const box = b('.pm-recent');
    if (!box) return;
    const item = document.createElement('span');
    item.textContent = text;
    box.prepend(item);
    while (box.children.length > RECENT_MAX) box.lastChild.remove();
  }

  /* ── Pre-call brief ─────────────────────────────── */
  let briefPoints = [];

  function sendBrief() {
    const notes = (briefFields.notes?.value || '').trim();
    const points = (briefFields.points?.value || '').split('\n').map((x) => x.trim()).filter(Boolean);
    const minutes = Number(briefFields.minutes?.value || 0);
    const client = (clientField?.value || '').trim();
    plannedMinutes = minutes;
    if (!notes && !points.length && !client) return;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'brief', notes, points, planned_minutes: minutes, client }));
    }
  }

  function renderPoints(points) {
    briefPoints = Array.isArray(points) ? points : [];
    if (bubble && bubble.dataset.state === 'listening' && b('.pm-hint')?.classList.contains('hidden')) {
      setTimeout(() => { if (bubble?.dataset.state === 'listening') b('.pm-mini').textContent = restingLabel(); }, 0);
    }
    const done = briefPoints.filter((p) => p.covered).length;
    const total = briefPoints.length;
    pointsPanel.classList.toggle('hidden', !total);
    pointsCount.textContent = total ? `${done}/${total} covered` : '';
    pointsList.innerHTML = briefPoints.map((p) => `<li class="${p.covered ? 'done' : ''}">${esc(p.text)}</li>`).join('');
    if (!bubble) return;
    const stat = b('.pm-points-stat');
    stat?.classList.toggle('hidden', !total);
    if (b('.pm-points-done')) b('.pm-points-done').textContent = `${done}/${total}`;
    const left = briefPoints.filter((p) => !p.covered).map((p) => p.text);
    const box = b('.pm-points');
    if (box) {
      box.classList.toggle('hidden', !total);
      box.querySelector('.pm-points-left').textContent = left.length
        ? left.slice(0, 2).join(' · ') + (left.length > 2 ? ` +${left.length - 2} more` : '')
        : 'All points covered ✓';
    }
  }

  function showBriefReminder(points) {
    if (!points?.length) return;
    nudgesEl.querySelector('.mt-empty')?.remove();
    const card = document.createElement('div');
    card.className = 'mt-nudge type-reminder';
    card.innerHTML = `
      <div class="mt-nudge-head"><span class="mt-line-time">${fmt(elapsed())}</span><b>Still to cover</b></div>
      <ol>${points.map((p) => `<li>${esc(p)}</li>`).join('')}</ol>`;
    nudgesEl.prepend(card);
    showNudge(`Still to cover: ${points.slice(0, 2).join(' · ')}${points.length > 2 ? ' …' : ''}`, 'alert');
  }

  /** Show a 3-point answer outline for the question the other person just asked. */
  function showHint(msg, label = '💡 Answer outline') {
    const points = (msg.points || []).filter(Boolean);
    if (!points.length) return;
    markActivity();
    setHelpBusy(null);
    // Page: keep every hint in the nudges column.
    nudgesEl.querySelector('.mt-empty')?.remove();
    const card = document.createElement('div');
    card.className = 'mt-nudge type-hint';
    card.innerHTML = `
      <div class="mt-nudge-head"><span class="mt-line-time">${fmt(msg.ts || elapsed())}</span><b>${esc(label.replace(/^\S+\s/, ''))}</b></div>
      <div class="mt-nudge-desc">${esc(msg.question || '')}</div>
      <ol>${points.map((p) => `<li>${esc(p)}</li>`).join('')}</ol>`;
    nudgesEl.prepend(card);
    if (!bubble) return;
    const box = b('.pm-hint');
    box.classList.toggle('is-help', !!msg.isHelp);
    box.querySelector('.pm-hint-label').textContent = label;
    box.querySelector('.pm-hint-q').textContent = msg.question ? `“${msg.question}”` : '';
    box.querySelector('.pm-hint-points').innerHTML = points.map((p) => `<li>${esc(p)}</li>`).join('');
    box.classList.remove('hidden');
    bubble.classList.add('has-hint');
    openDrop(true);
    scheduleHintClose();
    b('.pm-mini').textContent = `💡 ${points[0]}`;
    if (!['alert'].includes(bubble.dataset.state)) setBubbleState('hint', label.replace(/^\S+\s/, ''));
    fitPipWindow();
  }

  function setHelpBusy(kind) {
    if (!bubble) return;
    bubble.querySelectorAll('.pm-help-btn').forEach((btn) => { btn.disabled = !!kind && btn.dataset.help === kind; });
  }

  function askHelp(kind, btn) {
    if (!active || !ws || ws.readyState !== WebSocket.OPEN) return;
    markActivity();
    setHelpBusy(kind);
    ws.send(JSON.stringify({ type: 'help', kind }));
    setTimeout(() => setHelpBusy(null), 9000);   // re-enable even if no reply arrives
  }

  const MOOD_LABELS = {
    engaged: '🙂 engaged', positive: '😀 positive', neutral: '😐 neutral',
    confused: '🤔 confused', skeptical: '🧐 skeptical', disengaged: '😶 drifting',
  };

  function showMood(msg) {
    const chip = b('.pm-mood');
    if (!chip) return;
    chip.className = `pm-chip pm-mood mood-${msg.mood}`;
    chip.textContent = `Client: ${MOOD_LABELS[msg.mood] || msg.mood}`;
    chip.title = msg.reason || 'How the client comes across';
    if (msg.alert) showNudge(msg.alert, 'alert', { urgent: true });
  }

  const sttTrouble = { user: null, other: null };
  /** Transcript stream health: 'recovering' / 'failed' shows a small note; 'ok' clears it. */
  function showSttStatus(speaker, state) {
    sttTrouble[speaker === 'other' ? 'other' : 'user'] = state === 'ok' ? null : state;
    const who = (k) => (k === 'user' ? 'your' : "the client's");
    const msgs = Object.entries(sttTrouble).filter(([, v]) => v).map(([k, v]) =>
      v === 'failed' ? `Lost ${who(k)} transcript — check the connection.` : `Re-syncing ${who(k)} transcript…`);
    const el = b('.pm-stt-warn');
    if (el) {
      el.textContent = msgs.join(' ');
      el.classList.toggle('hidden', !msgs.length);
    }
    if (msgs.length) statusEl.textContent = msgs.join(' ');
    else if (active && !paused) statusEl.textContent = 'Listening';
  }

  function syncSizeButton(el = bubble) {
    const btn = el?.querySelector('.pm-size');
    if (!btn) return;
    const compact = el.dataset.size === 'compact';
    btn.textContent = compact ? '▢' : '▁';
    btn.title = compact ? 'Show the card' : 'Shrink to the pill';
    btn.setAttribute('aria-label', compact ? 'Show the card' : 'Shrink to the pill');
    const details = el.querySelector('.pm-details-btn');
    if (details && compact) { details.title = 'Show the card'; details.setAttribute('aria-label', 'Show the card'); }
  }

  /** Slim strip ↔ full card. In the PiP window the OS window is resized to fit. */
  function toggleSize() {
    if (!bubble) return;
    bubble.dataset.size = bubble.dataset.size === 'compact' ? 'full' : 'compact';
    try { localStorage.setItem(SIZE_KEY, bubble.dataset.size); } catch (_) { /* ignore */ }
    syncSizeButton();
    fitPipWindow();
  }

  function fitPipWindow() {
    if (!pipWindow || !bubble) return;
    requestAnimationFrame(() => {
      try {
        const wanted = Math.ceil(bubble.getBoundingClientRect().height) + 12;
        pipWindow.resizeBy(0, wanted - pipWindow.innerHeight);   // allowed inside a click
      } catch (_) { /* some browsers only allow user resizing */ }
    });
  }

  /** Open the bubble as an always-on-top Picture-in-Picture window (needs a click). */
  async function openPip() {
    if (!('documentPictureInPicture' in window)) return false;
    try {
      const compact = bubble?.dataset.size === 'compact';
      pipWindow = await window.documentPictureInPicture.requestWindow({ width: PIP_WIDTH, height: compact ? 62 : 300 });
    } catch (err) {
      console.warn('PiP window refused', err);
      pipWindow = null;
      return false;
    }
    const link = pipWindow.document.createElement('link');
    link.rel = 'stylesheet';
    link.href = new URL(document.querySelector('link[href*="bubble.css"]')?.getAttribute('href') || '/static/bubble.css', location.href).href;
    pipWindow.document.head.appendChild(link);
    pipWindow.document.title = 'Pitchlight coach';
    pipWindow.document.body.className = 'pm-pip-body';
    pipWindow.document.body.appendChild(bubble);
    bubble.classList.add('in-pip');
    bubble.classList.remove('floating');
    bubble.style.left = bubble.style.top = '';
    pipWindow.addEventListener('pagehide', () => {
      // User closed the mini window — keep coaching, park the bubble on this page.
      pipWindow = null;
      if (bubble) mountFloating();   // pull the bubble back before the PiP document goes away
      if (active) {
        reopenBtn.classList.remove('hidden');
        pipNoteEl.textContent = 'The floating bubble was closed. Coaching continues — pop it out again or keep this tab visible.';
      }
    });
    reopenBtn.classList.add('hidden');
    pipNoteEl.textContent = 'The coach bubble is floating on top of your screen. Drag it anywhere — it stays above Zoom, Meet and Teams.';
    return true;
  }

  /** Fallback: a draggable bubble on this page. */
  function mountFloating() {
    bubble.classList.remove('in-pip');
    bubble.classList.add('floating');
    document.body.appendChild(bubble);
    let pos = null;
    try { pos = JSON.parse(localStorage.getItem(BUBBLE_POS_KEY) || 'null'); } catch (_) { /* ignore */ }
    const maxX = window.innerWidth - 390, maxY = window.innerHeight - 60;
    const centre = Math.round((window.innerWidth - 380) / 2);   // notch: top centre by default
    bubble.style.left = `${Math.max(8, Math.min(maxX, pos?.x ?? centre))}px`;
    bubble.style.top = `${Math.max(8, Math.min(maxY, pos?.y ?? 12))}px`;
    enableDrag(bubble);
  }

  function enableDrag(el) {
    const handle = el.querySelector('.pm-drag');
    if (!handle || handle.dataset.dragBound) return;
    handle.dataset.dragBound = '1';
    let start = null;
    handle.addEventListener('pointerdown', (e) => {
      if (!el.classList.contains('floating') || e.target.closest('button')) return;
      start = { x: e.clientX, y: e.clientY, left: el.offsetLeft, top: el.offsetTop };
      el.dataset.dragged = '0';
      handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener('pointermove', (e) => {
      if (!start) return;
      if (Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) > 4) el.dataset.dragged = '1';
      const x = Math.max(0, Math.min(window.innerWidth - el.offsetWidth, start.left + e.clientX - start.x));
      const y = Math.max(0, Math.min(window.innerHeight - el.offsetHeight, start.top + e.clientY - start.y));
      el.style.left = `${x}px`;
      el.style.top = `${y}px`;
    });
    const stop = () => {
      if (!start) return;
      start = null;
      setTimeout(() => { el.dataset.dragged = '0'; }, 0);
      try { localStorage.setItem(BUBBLE_POS_KEY, JSON.stringify({ x: el.offsetLeft, y: el.offsetTop })); } catch (_) { /* ignore */ }
    };
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  }

  /* ── Live stats in the bubble ───────────────────── */
  function countWords(text) {
    const t = text.trim();
    return t ? t.split(/\s+/).length : 0;
  }

  function refreshStats() {
    if (!bubble) return;
    const now = Date.now();
    wordLog = wordLog.filter((w) => now - w.at <= 20000);
    const words = wordLog.reduce((n, w) => n + w.words, 0);
    const span = Math.min(20, Math.max(1, elapsed()));
    const wpm = elapsed() > 5 ? Math.round((words / span) * 60) : null;
    const wpmEl = b('.pm-wpm');
    if (wpmEl) {
      wpmEl.textContent = wpm == null ? '–' : String(wpm);
      wpmEl.classList.toggle('warn', wpm != null && wpm > 180);
    }
    const talkEl = b('.pm-talk');
    if (talkEl) {
      const voiced = youVoicedS + themVoicedS;
      const total = youWords + themWords;
      const share = otherStream && voiced >= 20 && themVoicedS > 0
        ? Math.round((youVoicedS / voiced) * 100)
        : (total >= 20 && themWords > 0 ? Math.round((youWords / total) * 100) : null);
      talkEl.textContent = share == null ? '–' : `${share}%`;
      talkEl.classList.toggle('warn', share != null && share > 75);   // monologue in a conversation
    }
    const fEl = b('.pm-fillers');
    if (fEl) fEl.textContent = String(fillerTotal);
    const eyeEl = b('.pm-eye');
    if (eyeEl) {
      eyeEl.textContent = fields.camera.checked ? (GAZE_TEXT[gazeState] || 'starting…') : 'camera off';
      eyeEl.classList.toggle('warn', gazeState && gazeState !== 'camera');
    }
    const t = b('.pm-timer');
    if (t) t.textContent = fmt(elapsed());
    renderAgenda();
    renderAnswerTimer();
    checkTalkShare();
    timerEl.textContent = fmt(elapsed());
  }

  let plannedMinutes = 0;

  function renderAgenda() {
    const bar = b('.pm-agenda');
    if (!bar) return;
    const total = briefPoints.length;
    const done = briefPoints.filter((p) => p.covered).length;
    const ring = b('.pm-ring');
    if (!plannedMinutes && !total) { bar.classList.add('hidden'); ring?.classList.add('hidden'); return; }
    bar.classList.remove('hidden');
    const mins = elapsed() / 60;
    const frac = plannedMinutes ? Math.min(1, mins / plannedMinutes) : (total ? done / total : 0);
    bar.querySelector('.pm-agenda-fill').style.width = `${(frac * 100).toFixed(1)}%`;
    if (ring) {
      // Pill ring: time used vs planned (or points covered when no length was set).
      ring.classList.remove('hidden');
      ring.querySelector('.pm-ring-fill').style.strokeDashoffset = String((56.55 * (1 - frac)).toFixed(2));
      ring.classList.toggle('warn', !!plannedMinutes && frac >= 0.8 && done < total);
      ring.title = [plannedMinutes ? `${Math.floor(mins)}/${plannedMinutes} min` : '', total ? `${done}/${total} points` : '']
        .filter(Boolean).join(' · ');
    }
    const parts = [];
    if (plannedMinutes) parts.push(`${Math.floor(mins)}/${plannedMinutes} min`);
    if (total) parts.push(`${done}/${total} points`);
    bar.querySelector('.pm-agenda-label').textContent = parts.join(' · ');
    bar.classList.toggle('warn', !!plannedMinutes && frac >= 0.8 && done < total);
  }

  function renderAnswerTimer() {
    const chip = b('.pm-answer');
    if (!chip) return;
    const now = Date.now();
    const answering = answerStart && now - lastYouVoice < 6000;
    chip.classList.toggle('hidden', !answering);
    if (!answering) return;
    const secs = (now - answerStart) / 1000;
    const limit = MONOLOGUE_LIMIT_S[fields.mode.value];
    longestMonologueS = Math.max(longestMonologueS, secs);
    chip.textContent = `Talking ${fmt(secs).replace(/^0/, '')}`;
    chip.classList.toggle('long', !!limit && secs >= limit);
    if (limit && secs >= limit && !answerWrapShown) {
      answerWrapShown = true;
      // The server records it (History + recap) and sends the nudge back through its cooldowns.
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'talk_time', kind: 'monologue', seconds: Math.round(secs) }));
      } else {
        showNudge(DEFAULT_CUES.monologue, 'alert');
      }
    }
  }

  /** "You've done most of the talking": over the last 5 minutes, once the other side is shared. */
  function checkTalkShare() {
    const now = Date.now();
    youVoiceTimes = youVoiceTimes.filter((t) => now - t <= TALK_SHARE_WINDOW_MS);
    themVoiceTimes = themVoiceTimes.filter((t) => now - t <= TALK_SHARE_WINDOW_MS);
    if (!ws || ws.readyState !== WebSocket.OPEN || paused) return;
    if (now - lastStatsSentAt >= TALK_STATS_EVERY_MS) sendTalkStats();
    if (!otherStream || !TALK_SHARE_MODES.includes(fields.mode.value) || now - lastShareReportAt < TALK_SHARE_GAP_MS) return;
    const you = youVoiceTimes.length * VOICE_CHUNK_S;
    const them = themVoiceTimes.length * VOICE_CHUNK_S;
    if (you + them < TALK_SHARE_MIN_S || them <= 0) return;
    const share = you / (you + them);
    if (share >= TALK_SHARE_LIMIT) {
      lastShareReportAt = now;
      ws.send(JSON.stringify({ type: 'talk_time', kind: 'talk_share', share: Number(share.toFixed(3)) }));
    }
  }

  function sendTalkStats() {
    lastStatsSentAt = Date.now();
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({
      type: 'talk_time', kind: 'stats', you_s: Number(youVoicedS.toFixed(1)),
      them_s: Number(themVoicedS.toFixed(1)), longest_monologue_s: Math.round(longestMonologueS),
    }));
  }

  /* ── Transcript + nudges on the page ───────────── */
  function lineFor(who) {
    if (activeLine && activeLine.who === who) return activeLine.el;
    transcriptEl.querySelector('.mt-empty')?.remove();
    const el = document.createElement('div');
    el.className = `mt-line is-${who}`;
    el.innerHTML = `<span class="mt-line-time">${fmt(elapsed())}</span>
      <span class="mt-line-who">${who === 'you' ? 'You' : 'Them'}</span>
      <span class="mt-line-text"><span class="final"></span><span class="interim"></span></span>`;
    transcriptEl.appendChild(el);
    activeLine = { who, el };
    return el;
  }

  function trackWords(before, after) {
    const added = countWords(after) - countWords(before);
    if (added > 0) wordLog.push({ at: Date.now(), words: added });
    fillerTotal += Math.max(0, (after.match(FILLER_RE) || []).length - (before.match(FILLER_RE) || []).length);
  }

  /** Text for one speaker. raw=true: streaming chunk carrying its own spacing. */
  function addTranscript(who, text, raw) {
    const clean = String(text || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
    if (!clean.trim()) return;
    const el = lineFor(who);
    const fin = el.querySelector('.final');
    const before = fin.textContent;
    fin.textContent = raw ? (before + clean).replace(/^\s+/, '') : `${before} ${clean.trim()}`.trim() + ' ';
    const added = Math.max(0, countWords(fin.textContent) - countWords(before));
    if (who === 'you') { trackWords(before, fin.textContent); youWords += added; } else { themWords += added; }
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
    refreshStats();
  }


  function addNudgeCard(msg) {
    nudgesEl.querySelector('.mt-empty')?.remove();
    const card = document.createElement('div');
    card.className = `mt-nudge type-${esc(msg.issue_type)}`;
    card.innerHTML = `
      <div class="mt-nudge-head"><span class="mt-line-time">${fmt(msg.ts || elapsed())}</span>
        <b>${esc(ISSUE_LABELS[msg.issue_type] || msg.issue_type)}</b></div>
      <div class="mt-nudge-desc">${esc(msg.description || '')}</div>
      <div class="mt-nudge-cue"></div>`;
    nudgesEl.prepend(card);
    eventCards.set(Number(msg.index), card);
  }

  function setCue(index, text) {
    const card = eventCards.get(Number(index));
    const cue = String(text || '').trim();
    if (card && cue) card.querySelector('.mt-nudge-cue').textContent = `“${cue}”`;
  }

  /* ── Server messages ────────────────────────────── */
  function onMessage(ev) {
    if (typeof ev.data !== 'string') return;   // meeting mode never plays coach audio
    let msg;
    try { msg = JSON.parse(ev.data); } catch (_) { return; }
    switch (msg.type) {
      case 'status':
        if (msg.session_id) sessionId = msg.session_id;
        if (msg.state === 'connected') {
          statusEl.textContent = 'Listening';
          setBubbleState('listening', 'Listening');
          if (b('.pm-mini')) b('.pm-mini').textContent = restingLabel();
        }
        else if (msg.state === 'error') { statusEl.textContent = msg.message || 'Connection error'; showNudge(msg.message || 'Connection error', 'alert'); }
        else if (msg.state === 'session_timeout') { statusEl.textContent = 'Time limit reached — saving'; }
        else if (msg.state === 'analyzing') { statusEl.textContent = 'Analyzing your meeting…'; setBubbleState('analyzing', 'Analyzing'); }
        break;
      case 'transcript':
        if (msg.speaker === 'other') addTranscript('them', msg.text, !!msg.raw);
        else if (msg.speaker === 'user') addTranscript('you', msg.text, !!msg.raw);
        break;
      case 'transcript_end':
        if (activeLine && activeLine.who === (msg.speaker === 'other' ? 'them' : 'you')) activeLine = null;
        break;
      case 'coach_tip':
        addNudgeCard(msg);
        showNudge(DEFAULT_CUES[msg.issue_type] || ISSUE_LABELS[msg.issue_type] || 'Check your delivery.',
          ['pace', 'eye_contact', 'off_topic', 'interruption'].includes(msg.issue_type) ? 'alert' : 'nudge',
          { index: Number(msg.index) });
        break;
      case 'answer_hint':
        showHint(msg);
        break;
      case 'help_reply':
        showHint({ points: msg.lines, isHelp: true }, msg.kind === 'ask' ? '? Question to ask' : msg.kind === 'catchup' ? '↺ Catch me up' : '💡 Help me answer');
        break;
      case 'client_mood':
        showMood(msg);
        break;
      case 'stt_status':
        showSttStatus(msg.speaker, msg.state);
        break;
      case 'brief_points':
        renderPoints(msg.points);
        break;
      case 'brief_reminder':
        showBriefReminder(msg.points);
        break;
      case 'commitments':
        renderCommitments(msg.items, msg.added);
        break;
      case 'previous_call':
        showPreviousCall(msg.call);
        break;
      case 'coach_cue':
        setCue(msg.index, msg.text);
        showNudge(msg.text, 'alert', { index: Number(msg.index) });
        break;
      case 'coach_said':
        setCue(msg.index, msg.text);
        break;
      case 'pipeline_step':
        statusEl.textContent = `Analyzing your meeting… (${String(msg.step || '').replace(/_/g, ' ')})`;
        break;
      case 'scorecard':
        showResult(msg.data || {});
        break;
      default:
        break;
    }
  }

  /* ── Commitments ────────────────────────────────── */
  function renderCommitments(items, added) {
    commitments = Array.isArray(items) ? items : [];
    const mine = commitments.filter((c) => c.owner === 'you');
    commitPanel.classList.toggle('hidden', !commitments.length);
    commitCount.textContent = commitments.length
      ? `${mine.length} by you · ${commitments.length - mine.length} by them` : '';
    commitList.innerHTML = commitments.map((c) => `
      <li class="${c.owner === 'you' ? 'is-you' : 'is-them'}">
        <span class="mt-line-time">${fmt(c.ts || 0)}</span>
        <b>${c.owner === 'you' ? 'You' : 'Them'}</b> ${esc(c.task)}${c.due ? ` <em>· ${esc(c.due)}</em>` : ''}
      </li>`).join('');
    const row = b('.pm-commits');
    if (row) {
      row.classList.toggle('hidden', !mine.length);
      const latest = mine[mine.length - 1];
      row.querySelector('.pm-commits-text').textContent = latest
        ? `${latest.task}${latest.due ? ` (${latest.due})` : ''}${mine.length > 1 ? ` +${mine.length - 1} more` : ''}` : '';
    }
    const newMine = (added || []).filter((c) => c.owner === 'you');
    if (newMine.length) {
      const c = newMine[newMine.length - 1];
      showNudge(`🤝 Noted: ${c.task}${c.due ? ` (${c.due})` : ''}`, 'nudge', { urgent: true });
    }
    fitPipWindow();
  }

  /* ── Past-call memory ───────────────────────────── */
  function apiHeaders(json) {
    const h = { 'x-user-id': USER_ID };
    const token = (localStorage.getItem('pitchmirror_api_token') || '').trim();
    if (token) h.Authorization = `Bearer ${token}`;
    if (json) h['Content-Type'] = 'application/json';
    return h;
  }

  const callDate = (ts) => (ts ? new Date(Number(ts) * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '');

  /** During the call: the last call's open items, once, as a hint card. */
  function showPreviousCall(call) {
    if (!call) return;
    const points = [
      ...(call.promised || []).slice(0, 2).map((t) => `You promised: ${t}`),
      ...(call.open_questions || []).slice(0, 2).map((t) => `Still open: ${t}`),
      ...(call.objections || []).slice(0, 1).map((t) => `Their concern: ${t}`),
    ].slice(0, 3);
    if (points.length) showHint({ points, question: call.summary ? call.summary.split(/(?<=\.)\s/)[0] : '' }, `↺ Last call · ${callDate(call.created_at)}`);
  }

  async function loadClients() {
    if (!clientsList) return;
    try {
      const resp = await fetch('/api/clients', { headers: apiHeaders() });
      if (!resp.ok) return;
      const { clients } = await resp.json();
      clientsList.innerHTML = (clients || []).map((c) => `<option value="${esc(c.name)}">`).join('');
    } catch (_) { /* offline: no suggestions */ }
  }

  let lastCallTimer = null, lastCallFor = '';
  function scheduleLastCall() {
    clearTimeout(lastCallTimer);
    lastCallTimer = setTimeout(loadLastCall, 500);
  }

  async function loadLastCall() {
    const name = (clientField?.value || '').trim();
    if (name.toLowerCase() === lastCallFor) return;
    lastCallFor = name.toLowerCase();
    if (name.length < 2) { lastCallEl.classList.add('hidden'); return; }
    try {
      const resp = await fetch(`/api/clients/last?name=${encodeURIComponent(name)}`, { headers: apiHeaders() });
      const call = resp.ok ? (await resp.json()).call : null;
      if (lastCallFor !== name.toLowerCase()) return;   // typed on meanwhile
      if (!call || !call.session_id) {
        lastCallEl.classList.add('hidden');
        return;
      }
      const list = (title, items) => (items && items.length
        ? `<div class="mt-lc-block"><b>${title}</b><ul>${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '');
      lastCallEl.innerHTML = `
        <div class="mt-lc-head">↺ Last call with ${esc(call.client || name)} · ${esc(callDate(call.created_at))}
          <a href="/app?session=${encodeURIComponent(call.session_id)}" target="_blank" rel="noopener">Open review →</a></div>
        ${call.summary ? `<p class="mt-lc-summary">${esc(call.summary)}</p>` : ''}
        <div class="mt-lc-grid">
          ${list('You promised', call.promised)}
          ${list('Still open', call.open_questions)}
          ${list('Their concerns', call.objections)}
        </div>
        <p class="mt-lc-note">The coach will remind you of these when the call starts.</p>`;
      lastCallEl.classList.remove('hidden');
    } catch (_) {
      lastCallEl.classList.add('hidden');
    }
  }

  /* ── Pre-call rehearsal ─────────────────────────── */
  const REHEARSE_ANSWER_S = 40;   // ~3 questions in about 2 minutes
  let rehearsal = null;           // { questions, idx, answers[], listener, qStartedAt, graceUntil, timer }

  function briefPayload() {
    return {
      mode: fields.mode.value,
      client: (clientField?.value || '').trim(),
      notes: (briefFields.notes?.value || '').trim(),
      points: (briefFields.points?.value || '').split('\n').map((x) => x.trim()).filter(Boolean),
    };
  }

  async function startRehearsal() {
    if (rehearsal) { stopRehearsal(); return; }
    rehearseBody.classList.remove('hidden');
    rehearseBody.innerHTML = '<div class="mt-rh-wait">Picking the hardest questions for this call…</div>';
    rehearseBtn.disabled = true;
    let questions = [];
    try {
      const resp = await fetch('/api/rehearsal/questions', {
        method: 'POST', headers: apiHeaders(true), body: JSON.stringify(briefPayload()),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      questions = (await resp.json()).questions || [];
    } catch (err) {
      rehearseBody.innerHTML = '<div class="mt-rh-error">Couldn\'t prepare questions right now — try again in a moment.</div>';
      rehearseBtn.disabled = false;
      return;
    }
    rehearsal = { questions, idx: 0, answers: questions.map(() => ''), listener: null, qStartedAt: Date.now(), graceUntil: 0, timer: null };
    rehearseBtn.disabled = false;
    rehearseBtn.textContent = 'Stop rehearsal';
    renderRehearsal();
    try {
      rehearsal.listener = await window.PMRehearse.listen({
        userId: USER_ID,
        onText: onRehearsalText,
        onStatus: (st, message) => {
          if (st === 'failed' && rehearsal) {
            rehearseBody.querySelector('.mt-rh-live')?.replaceChildren(document.createTextNode(message || 'Speech-to-text stopped working.'));
          }
        },
      });
    } catch (err) {
      stopRehearsal(true);
      rehearseBody.innerHTML = '<div class="mt-rh-error">Microphone access was blocked, so the rehearsal can\'t hear you. Allow the mic and try again.</div>';
      return;
    }
    rehearsal.timer = setInterval(renderRehearsalTimer, 500);
    askCurrentQuestion();
  }

  async function askCurrentQuestion() {
    const r = rehearsal;
    if (!r) return;
    r.qStartedAt = Date.now();
    r.graceUntil = Math.max(r.graceUntil, Date.now() + 3000);
    renderRehearsal();
    await window.PMRehearse.speak(r.questions[r.idx].question);
    if (rehearsal === r) {
      // Lines that come back right after the question was read are the previous answer's tail.
      r.graceUntil = Math.max(r.graceUntil, Date.now() + 800);
      r.qStartedAt = Date.now();
    }
  }

  function onRehearsalText(text) {
    const r = rehearsal;
    if (!r) return;
    // Laptop speakers: the mic can pick up the question being read out — drop that echo.
    const echo = r.questions.some((q) => {
      const m = window.PMRehearse.keywordMatch(q.question, text);
      return m && m.total >= 3 && m.hit / m.total >= 0.6 && text.split(/\s+/).length <= q.question.split(/\s+/).length + 3;
    });
    if (echo) return;
    const target = Date.now() < r.graceUntil && r.idx > 0 && !r.finishing ? r.idx - 1 : r.idx;
    r.answers[target] = `${r.answers[target]} ${text}`.trim();
    renderRehearsalAnswer();
  }

  function renderRehearsal() {
    const r = rehearsal;
    if (!r) return;
    const q = r.questions[r.idx];
    const last = r.idx === r.questions.length - 1;
    rehearseBody.innerHTML = `
      <div class="mt-rh-top">
        <span class="mt-rh-step">Question ${r.idx + 1} of ${r.questions.length}</span>
        <span class="mt-rh-dots">${r.questions.map((_, i) => `<i class="${i < r.idx ? 'done' : i === r.idx ? 'now' : ''}"></i>`).join('')}</span>
        <span class="mt-rh-timer">0:00</span>
      </div>
      <div class="mt-rh-q">“${esc(q.question)}”</div>
      ${q.why ? `<div class="mt-rh-why">Why it's hard: ${esc(q.why)}</div>` : ''}
      <div class="mt-rh-live"><span class="mt-rh-label">Your answer</span><span class="mt-rh-answer">Listening — answer out loud…</span></div>
      <div class="mt-rh-actions">
        <button type="button" class="mt-btn mt-btn-ghost mt-rh-repeat">🔊 Repeat question</button>
        <button type="button" class="mt-btn mt-btn-primary mt-rh-next">${last ? 'Finish & get feedback' : 'Next question →'}</button>
      </div>`;
    rehearseBody.querySelector('.mt-rh-repeat').addEventListener('click', () => window.PMRehearse.speak(q.question));
    rehearseBody.querySelector('.mt-rh-next').addEventListener('click', nextRehearsalQuestion);
    renderRehearsalAnswer();
  }

  function renderRehearsalAnswer() {
    const r = rehearsal;
    const el = rehearseBody.querySelector('.mt-rh-answer');
    if (!r || !el) return;
    const text = r.answers[r.idx];
    el.textContent = text || 'Listening — answer out loud…';
    el.classList.toggle('is-empty', !text);
  }

  function renderRehearsalTimer() {
    const r = rehearsal;
    const el = rehearseBody.querySelector('.mt-rh-timer');
    if (!r || !el) return;
    const secs = (Date.now() - r.qStartedAt) / 1000;
    el.textContent = fmt(secs).replace(/^0/, '');
    el.classList.toggle('long', secs >= REHEARSE_ANSWER_S);
  }

  function nextRehearsalQuestion() {
    const r = rehearsal;
    if (!r) return;
    window.PMRehearse.stopSpeaking();
    if (r.idx < r.questions.length - 1) {
      r.idx += 1;
      askCurrentQuestion();
    } else {
      finishRehearsal();
    }
  }

  async function finishRehearsal() {
    const r = rehearsal;
    if (!r) return;
    r.finishing = true;
    clearInterval(r.timer);
    rehearseBody.innerHTML = '<div class="mt-rh-wait">Catching your last words, then reviewing your answers…</div>';
    try { await r.listener?.stop(); } catch (_) { /* ignore */ }
    rehearsal = null;
    rehearseBtn.textContent = 'Rehearse again';
    const items = r.questions.map((q, i) => ({ question: q.question, answer: r.answers[i] }));
    if (!items.some((it) => it.answer)) {
      rehearseBody.innerHTML = '<div class="mt-rh-error">No answers were heard. Check the microphone (headphones help) and try again.</div>';
      return;
    }
    let review = [];
    try {
      const resp = await fetch('/api/rehearsal/review', {
        method: 'POST', headers: apiHeaders(true), body: JSON.stringify({ mode: fields.mode.value, items }),
      });
      if (resp.ok) review = (await resp.json()).review || [];
    } catch (_) { /* show answers without a review */ }
    renderRehearsalReview(items, review);
  }

  function renderRehearsalReview(items, review) {
    const byQ = new Map(review.map((x) => [x.question, x]));
    rehearseBody.innerHTML = `<div class="mt-rh-review">${items.map((it, i) => {
      const rv = byQ.get(it.question) || {};
      const stars = Number.isFinite(rv.rating) ? '★'.repeat(rv.rating) + '☆'.repeat(5 - rv.rating) : '';
      return `
        <div class="mt-rh-card">
          <div class="mt-rh-card-head"><b>Q${i + 1}</b> ${stars ? `<span class="mt-rh-stars">${stars}</span>` : ''}</div>
          <div class="mt-rh-q small">“${esc(it.question)}”</div>
          <div class="mt-rh-cols">
            <div><span class="mt-rh-label">You said</span><p>${esc(it.answer || '(no answer heard)')}</p></div>
            ${rv.better_answer ? `<div class="is-better"><span class="mt-rh-label">Stronger answer
              <button type="button" class="mt-rh-say" data-i="${i}" title="Read it aloud">🔊</button></span>
              <p>${esc(rv.better_answer)}</p></div>` : ''}
          </div>
          ${rv.improve ? `<div class="mt-rh-fix"><b>Fix:</b> ${esc(rv.improve)}</div>` : ''}
        </div>`;
    }).join('')}</div>`;
    rehearseBody.querySelectorAll('.mt-rh-say').forEach((btn) => btn.addEventListener('click', () => {
      const rv = byQ.get(items[Number(btn.dataset.i)].question);
      if (rv?.better_answer) window.PMRehearse.speak(rv.better_answer);
    }));
  }

  function stopRehearsal(silent) {
    const r = rehearsal;
    rehearsal = null;
    if (!r) return;
    clearInterval(r.timer);
    window.PMRehearse.stopSpeaking();
    r.listener?.stop().catch(() => {});
    rehearseBtn.textContent = 'Start rehearsal';
    if (!silent) rehearseBody.innerHTML = '<div class="mt-rh-wait">Rehearsal stopped.</div>';
  }

  /* ── Capture ────────────────────────────────────── */
  function toPcm16(f32) {
    const pcm = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const v = Math.max(-1, Math.min(1, f32[i]));
      pcm[i] = v < 0 ? v * 0x8000 : v * 0x7FFF;
    }
    return pcm.buffer;
  }

  /** Microphone → levelled → server (transcription + voice measurements). */
  async function startAudio() {
    audioCtx = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
    // Audio chunks arrive every 100 ms even when Chrome throttles timers in a
    // background window, so they also drive the gaze checks and stats.
    worklet = await window.PMAudio.createLeveler(audioCtx, stream, (chunk) => {
      if (!active || !ws || ws.readyState !== WebSocket.OPEN) return;
      if (!paused) ws.send(frame(T_AUDIO_IN, toPcm16(chunk.pcm)));
      trackVoice('you', chunk.raw);
      pulse();
    }, { kind: 'mic', chunkSamples: AUDIO_CHUNK_SAMPLES });
  }

  function frame(type, buf) {
    const out = new Uint8Array(1 + buf.byteLength);
    out[0] = type;
    out.set(new Uint8Array(buf), 1);
    return out.buffer;
  }

  function rms(f32) {
    let sum = 0;
    for (let i = 0; i < f32.length; i++) sum += f32[i] * f32[i];
    return Math.sqrt(sum / Math.max(1, f32.length));
  }

  /** Voice activity per side → meters, answer timer, interruption detection. */
  function trackVoice(side, level) {
    const now = Date.now();
    if (side === 'you') {
      youLevel = level;
      if (level >= YOU_VOICE_RMS && !paused) { youVoicedS += VOICE_CHUNK_S; youVoiceTimes.push(now); }
      if (level >= YOU_VOICE_RMS) {
        if (now - lastYouVoice > 600) youTalkStart = now;
        // A new answer starts after the client had the floor, or after you paused for 4 s.
        if (!answerStart || now - lastYouVoice > 4000) {
          answerStart = now;
          answerWrapShown = false;
        }
        lastYouVoice = now;
      }
    } else {
      themLevel = level;
      if (level >= THEM_VOICE_RMS && !paused) { themVoicedS += VOICE_CHUNK_S; themVoiceTimes.push(now); }
      if (level >= THEM_VOICE_RMS) {
        if (now - lastThemVoice > 600) themTalkStart = now;
        lastThemVoice = now;
        if (now - themTalkStart >= 1000) answerStart = 0;   // they have the floor
      }
    }
    // Interruption: you start talking while they are mid-sentence, and keep going.
    const bothTalking = now - lastYouVoice < 250 && now - lastThemVoice < 250;
    if (bothTalking && youTalkStart > themTalkStart + 300) {
      if (!overlapStart) overlapStart = now;
      if (now - overlapStart >= INTERRUPT_OVERLAP_MS && now - lastInterruptAt > 20000 && ws?.readyState === WebSocket.OPEN) {
        lastInterruptAt = now;
        ws.send(JSON.stringify({ type: 'interruption', overlap_s: (now - overlapStart) / 1000 }));
      }
    } else if (!bothTalking) {
      overlapStart = 0;
    }
  }

  function renderMeters() {
    const you = b('.pm-meter-you');
    const them = b('.pm-meter-them');
    if (you) you.style.width = `${Math.min(100, youLevel * 900).toFixed(0)}%`;
    if (them) them.style.width = `${Math.min(100, themLevel * 1400).toFixed(0)}%`;
    const off = b('.pm-them-off');
    if (off) off.classList.toggle('hidden', !!otherStream);
  }

  let lastStatsAt = 0;
  function pulse() {
    const now = Date.now();
    if (now - lastStatsAt >= 1000) { lastStatsAt = now; refreshStats(); }
    renderMeters();
    // Smart quiet: show a held tip once the client pauses.
    if (heldNudge && !themSpeaking()) {
      const h = heldNudge;
      heldNudge = null;
      showNudge(h.text, h.level, { ...h.opts, urgent: true });
    }
    if (prefs.dim && bubble && now - lastActivityAt > IDLE_DIM_MS && !heldNudge) bubble.classList.add('is-idle');
    if (fields.camera.checked && !paused) window.GazeTracker?.tick();
  }

  /** Other participant's audio (meeting tab / system audio) → levelled → server. */
  async function startOtherAudio() {
    if (!otherStream || !otherStream.getAudioTracks().length) return;
    otherCtx = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
    otherWorklet = await window.PMAudio.createLeveler(otherCtx, otherStream, (chunk) => {
      if (!active || paused || !ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(frame(T_OTHER_AUDIO_IN, toPcm16(chunk.pcm)));
      trackVoice('them', chunk.raw);
    }, { kind: 'meeting', chunkSamples: AUDIO_CHUNK_SAMPLES });
  }

  /* ── Lifecycle ──────────────────────────────────── */
  async function startMeeting() {
    showError('');
    saveSettings();
    if (rehearsal) stopRehearsal(true);
    startBtn.disabled = true;

    // The PiP window must be requested inside the click, before any other await.
    bubble = createBubble();
    const inPip = await openPip();
    if (!inPip) {
      mountFloating();
      pipNoteEl.textContent = 'Your browser can\'t float the bubble over other apps, so it floats on this page. '
        + 'Place this window next to your meeting (Chrome or Edge on desktop supports the floating mini window).';
    }
    setBubbleState('idle', 'Starting…');

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { ...window.PMAudio.MIC_CONSTRAINTS, sampleRate: AUDIO_SAMPLE_RATE },
        video: fields.camera.checked ? { width: VIDEO_W, height: VIDEO_H, facingMode: 'user' } : false,
      });
    } catch (err) {
      console.error(err);
      cleanupBubble();
      startBtn.disabled = false;
      showError(fields.camera.checked
        ? 'Microphone/camera access was blocked. If your meeting app is using the camera exclusively, untick "Use my camera" and try again.'
        : 'Microphone access was blocked. Allow it in the address bar and try again.');
      return;
    }

    if (fields.camera.checked) {
      videoEl.srcObject = stream;
      try { await videoEl.play(); } catch (_) { /* autoplay muted is allowed */ }
    }

    const url = new URL(WS_URL);
    url.searchParams.set('kind', 'meeting');
    url.searchParams.set('platform', fields.platform.value);
    url.searchParams.set('mode', fields.mode.value);
    url.searchParams.set('persona', fields.persona.value);
    url.searchParams.set('context', 'virtual');
    url.searchParams.set('user', USER_ID);
    const token = (localStorage.getItem('pitchmirror_api_token') || '').trim();
    if (token) url.searchParams.set('token', token);

    ws = new WebSocket(url.toString());
    ws.binaryType = 'arraybuffer';
    ws.onmessage = onMessage;
    ws.onopen = async () => {
      active = true;
      startedAt = Date.now();
      setupEl.classList.add('hidden');
      liveEl.classList.remove('hidden');
      document.title = '● Coaching — Pitchlight';
      window.__pmSessionActive = true;
      sendBrief();
      await startAudio();
      await startOtherAudio();
      if (!otherStream) {
        pipNoteEl.textContent += ' Only your voice is coached — the other person isn\'t being captured.';
      }
      if (fields.camera.checked && window.GazeTracker) {
        window.GazeTracker.start(videoEl, (state) => {
          gazeState = state;
          if (ws && ws.readyState === WebSocket.OPEN && active && !paused) ws.send(JSON.stringify({ type: 'gaze', state }));
        }, { manualTick: true });
      }
      timerHandle = setInterval(refreshStats, 1000);
      refreshStats();
    };
    ws.onerror = () => { statusEl.textContent = 'Connection problem'; };
    ws.onclose = () => {
      if (active) {
        // Closed by the server mid-meeting (limit, error). Results may still be saved.
        stopCapture();
        statusEl.textContent = 'Disconnected — check History for anything saved.';
        setBubbleState('alert', 'Disconnected');
        showNudge('Connection lost.', 'alert');
      } else if (ending && !resultEl.innerHTML) {
        statusEl.textContent = 'Saved. Open History to see the review.';
      }
    };
  }

  function togglePause() {
    if (!active) return;
    paused = !paused;
    const btn = b('.pm-pause');
    if (btn) { btn.textContent = paused ? '▶' : '❚❚'; btn.title = paused ? 'Resume listening' : 'Pause listening'; }
    setBubbleState(paused ? 'paused' : 'listening', paused ? 'Paused' : 'Listening');
    b('.pm-nudge').textContent = paused ? 'Paused — not listening.' : 'Listening again.';
    b('.pm-mini').textContent = restingLabel();
    statusEl.textContent = paused ? 'Paused' : 'Listening';
  }

  function stopCapture() {
    active = false;
    window.__pmSessionActive = false;
    if (timerHandle) { clearInterval(timerHandle); timerHandle = null; }
    window.GazeTracker?.stop();
    if (worklet) { worklet.port.onmessage = null; worklet.pmSource?.disconnect(); worklet.disconnect(); worklet = null; }
    if (audioCtx) { audioCtx.close().catch(() => {}); audioCtx = null; }
    if (otherWorklet) { otherWorklet.port.onmessage = null; otherWorklet.pmSource?.disconnect(); otherWorklet.disconnect(); otherWorklet = null; }
    if (otherCtx) { otherCtx.close().catch(() => {}); otherCtx = null; }
    if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
    if (otherStream) { otherStream.getTracks().forEach((t) => t.stop()); otherStream = null; }
    videoEl.srcObject = null;
    document.title = 'Pitchlight — Meeting Mode';
  }

  function endMeeting() {
    if (!active || ending) return;
    ending = true;
    refreshStats();
    sendTalkStats();
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(new Uint8Array([T_STOP]).buffer);
    stopCapture();
    endBtn.disabled = true;
    statusEl.textContent = 'Analyzing your meeting… this takes about a minute.';
    setBubbleState('analyzing', 'Analyzing');
    if (b('.pm-nudge')) b('.pm-nudge').textContent = 'Meeting ended. Building your review…';
    if (b('.pm-mini')) b('.pm-mini').textContent = 'Building your review…';
    openDrop(true);
  }

  function showResult(data) {
    const insufficient = !!data.insufficient_speech;
    const score = insufficient ? '–' : Number(data.overall_score || 0).toFixed(1);
    const sid = data.session_id || sessionId;
    const reviewUrl = `/app?session=${encodeURIComponent(sid || '')}`;
    const moments = (data.coaching_events || []).length;
    const words = (data.transcript || []).filter((s) => s.s === 'u')
      .reduce((n, s) => n + countWords(String(s.t || '')), 0);
    statusEl.textContent = 'Meeting saved';
    resultEl.classList.remove('hidden');
    resultEl.innerHTML = `
      <div class="mt-result-score">${esc(score)}<span>/100</span></div>
      ${insufficient ? '<div class="mt-result-meta"><b>Not enough of your speech was captured to score this meeting.</b> Check the microphone and the language setting.</div>' : ''}
      <div class="mt-result-meta">${fmt(Number(data.duration_seconds) || 0)} · ${words} words · ${moments} coaching moment${moments === 1 ? '' : 's'}</div>
      <a class="mt-btn mt-btn-primary" href="${esc(reviewUrl)}">Open transcript &amp; full review</a>
      <button type="button" class="mt-btn mt-btn-ghost" id="mt-again">Coach another meeting</button>`;
    $('mt-again').addEventListener('click', () => location.reload());
    endBtn.classList.add('hidden');
    reopenBtn.classList.add('hidden');
    if (bubble) {
      setBubbleState('done', 'Saved');
      b('.pm-nudge').textContent = insufficient
        ? 'Too little speech captured to score.'
        : `Score ${score}/100 — your review is ready.`;
      const link = b('.pm-review');
      link.href = new URL(reviewUrl, location.href).href;
      link.classList.remove('hidden');
      b('.pm-mini').textContent = insufficient ? 'Too little speech to score' : `Score ${score}/100 — review ready`;
      openDrop(true);
    }
    ending = false;
  }

  function cleanupBubble() {
    if (pipWindow) { try { pipWindow.close(); } catch (_) { /* ignore */ } pipWindow = null; }
    bubble?.remove();
    bubble = null;
  }

  async function chooseOtherAudio() {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      otherStatusEl.textContent = 'This browser can\'t capture meeting audio.';
      otherStatusEl.className = 'mt-other-status bad';
      return;
    }
    try {
      const s = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 1 },
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
        systemAudio: 'include',
        selfBrowserSurface: 'exclude',
        preferCurrentTab: false,
      });
      if (!s.getAudioTracks().length) {
        s.getTracks().forEach((t) => t.stop());
        otherStatusEl.textContent = 'No audio was shared. Try again and tick “Share tab audio” (or “Share system audio”).';
        otherStatusEl.className = 'mt-other-status bad';
        return;
      }
      if (otherStream) otherStream.getTracks().forEach((t) => t.stop());
      otherStream = s;
      const label = s.getVideoTracks()[0]?.label || 'meeting audio';
      otherStatusEl.textContent = `Connected: ${label} ✓ — the coach will hear the other person.`;
      otherStatusEl.className = 'mt-other-status ok';
      otherBtn.textContent = 'Change meeting audio';
      s.getAudioTracks()[0].addEventListener('ended', () => {
        otherStatusEl.textContent = 'Meeting audio sharing stopped.';
        otherStatusEl.className = 'mt-other-status bad';
      });
    } catch (err) {
      otherStatusEl.textContent = 'Not connected — only your voice will be coached.';
      otherStatusEl.className = 'mt-other-status';
    }
  }

  otherBtn.addEventListener('click', chooseOtherAudio);
  clientField?.addEventListener('input', scheduleLastCall);
  clientField?.addEventListener('change', loadLastCall);
  rehearseBtn?.addEventListener('click', startRehearsal);
  loadClients();
  if (clientField?.value) loadLastCall();
  startBtn.addEventListener('click', startMeeting);
  endBtn.addEventListener('click', endMeeting);
  reopenBtn.addEventListener('click', async () => {
    if (!bubble) return;
    if (!(await openPip())) reopenBtn.classList.add('hidden');
  });
  fields.camera.addEventListener('change', () => { if (fields.camera.checked) window.GazeTracker?.preload(); });
  window.addEventListener('beforeunload', (e) => {
    if (active) { e.preventDefault(); e.returnValue = ''; }
  });
})();
