/**
 * PitchMirror — Browser client
 *
 * Responsibilities:
 * - Capture webcam + microphone via getUserMedia
 * - Optionally capture screen-share frames via getDisplayMedia
 * - Encode audio as raw 16-bit PCM at 16kHz and send to backend via WebSocket
 * - Capture webcam JPEG frames at 1fps and optional screen JPEG frames at 0.5fps
 * - Play coach audio responses (24kHz PCM) via Web Audio API
 * - Update live metrics, transcript feed, and scorecard
 */

/* ── Config ──────────────────────────────────────── */
const WS_BASE_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
const AUDIO_SAMPLE_RATE = 16000;
const AUDIO_CHUNK_MS = 100;        // Send 100ms chunks
const VIDEO_FPS = 1;               // 1 frame per second (Live API max)
const VIDEO_QUALITY = 0.7;         // JPEG quality
const VIDEO_WIDTH = 640;
const VIDEO_HEIGHT = 480;
const SCREEN_FPS = 0.5;            // 1 frame every 2s for lower cost
const SCREEN_QUALITY = 0.72;
const COACH_SAMPLE_RATE = 24000;   // Gemini outputs 24kHz PCM
const COACH_AUDIO_SCHEDULE_LEAD_S = 0.02;
const COACH_AUDIO_RESET_GAP_S = 0.35;
const COACH_AUDIO_MAX_BUFFER_AHEAD_S = 1.25;
const SUPPORTS_SCREEN_SHARE = !!navigator.mediaDevices?.getDisplayMedia;

/* ── Binary frame type IDs (must match backend/main.py) ── */
const T_AUDIO_IN  = 0x01;  // client→server PCM
const T_VIDEO_IN  = 0x02;  // client→server JPEG
const T_STOP      = 0x03;  // client→server stop
const T_SCREEN_IN = 0x04;  // client→server screen JPEG
const T_SLIDE_IN  = 0x05;  // client→server uploaded slide JPEG
const T_AUDIO_OUT = 0x10;  // server→client coach PCM
const CLIENT_USER_ID_KEY = 'pitchmirror_user_id';
const CLIENT_USER_ID = (() => {
  const existing = window.localStorage.getItem(CLIENT_USER_ID_KEY);
  if (existing && /^[A-Za-z0-9_.-]{3,64}$/.test(existing)) return existing;
  const generated = `user-${(window.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)).slice(0, 12)}`;
  window.localStorage.setItem(CLIENT_USER_ID_KEY, generated);
  return generated;
})();

/** Prepend a 1-byte type header to a payload ArrayBuffer. */
function makeFrame(typeId, payloadBuffer) {
  const frame = new Uint8Array(1 + payloadBuffer.byteLength);
  frame[0] = typeId;
  frame.set(new Uint8Array(payloadBuffer), 1);
  return frame.buffer;
}

function authHeaders() {
  const headers = { 'x-user-id': CLIENT_USER_ID };
  const token = getApiToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

function buildWsUrl(sessionCfg = {}) {
  const url = new URL(WS_BASE_URL);
  const token = getApiToken();
  if (token) url.searchParams.set('token', token);
  if (CLIENT_USER_ID) url.searchParams.set('user', CLIENT_USER_ID);
  if (sessionCfg.persona) url.searchParams.set('persona', sessionCfg.persona);
  if (sessionCfg.mode) url.searchParams.set('mode', sessionCfg.mode);
  if (sessionCfg.deliveryContext) url.searchParams.set('context', sessionCfg.deliveryContext);
  if (sessionCfg.primaryGoal) url.searchParams.set('goal', sessionCfg.primaryGoal);
  if (sessionCfg.screenEnabled) url.searchParams.set('screen', '1');
  if (sessionCfg.demoMode) url.searchParams.set('demo', '1');
  return url.toString();
}

function getApiToken() {
  const raw =
    window.PITCHMIRROR_API_TOKEN ??
    window.localStorage.getItem('pitchmirror_api_token') ??
    '';
  return String(raw).trim();
}

/* ── State ───────────────────────────────────────── */
let ws = null;
let localStream = null;
let audioContext = null;
let audioWorklet = null;
let audioWorkletSink = null;
let videoCanvas = null;
let videoInterval = null;
let screenStream = null;
let screenCanvas = null;
let screenVideo = null;
let screenInterval = null;
let coachAudioContext = null;
let coachNextPlayAt = 0;
let sessionActive = false;
let metrics = { filler: 0, eye: 0, pace: 0, clarity: 0, visuals: 0 };
const practiceSlides = [
  {
    title: 'Slide 1: Problem',
    body: 'State the pain clearly in one sentence and who experiences it.',
  },
  {
    title: 'Slide 2: Why Now',
    body: 'Explain why this matters today with one concrete trigger.',
  },
  {
    title: 'Slide 3: Solution',
    body: 'Describe your approach in plain language and one key advantage.',
  },
  {
    title: 'Slide 4: Evidence',
    body: 'Show one metric, one result, and one proof point.',
  },
  {
    title: 'Slide 5: Ask',
    body: 'Close with a specific ask and the exact next step.',
  },
];
let currentSlideIndex = 0;
let uploadedSlides = [];
let uploadedDeckId = '';
let currentUploadedSlideIndex = 0;

/* Overlay Drawing */
let overlayCtx = null;

/* Timer state */
let timerInterval = null;
let sessionStartTime = null;

/* Active transcript line — accumulates streaming chunks from the same speaker */
let _activeLine = { speaker: null, el: null };
const COACH_DEDUP_WINDOW_MS = 12000;
let _lastCoachLine = { normalized: '', at: 0 };
// Gemini Live often sends a second input_transcription for the same utterance
// after the model responds (deferred final). Dedup within 10s prevents it showing twice.
const USER_DEDUP_WINDOW_MS = 10000;
let _lastUserLine = { normalized: '', at: 0 };

/* Live coach suggestions — one card per coaching moment, keyed by server event index */
const _suggestionCards = new Map();
const _pendingCoachSaid = new Map();
let _suggestionCount = 0;
const SUGGESTIONS_EMPTY_HTML = `
  <div class="suggestions-empty">
    Coaching moments show up here while you speak — what was measured,
    what the coach said, and how to fix it.
  </div>`;

/* Session recovery — used to re-fetch scorecard if WS closes during analysis */
let _currentSessionId = null;
let _waitingForScorecard = false;
let _replayTimeline = { markers: [], duration: 0 };

/* ── DOM refs ────────────────────────────────────── */
const video           = document.getElementById('local-video');
const overlayCanvas   = document.getElementById('overlay-canvas');
const btnStart        = document.getElementById('btn-start');
const btnStop         = document.getElementById('btn-stop');
const statusBadge     = document.getElementById('status-badge');
const liveMetrics     = document.getElementById('live-metrics');
const videoOverlay    = document.getElementById('video-overlay');
const recIndicator    = document.getElementById('recording-indicator');
const transcriptFeed  = document.getElementById('transcript-feed');
const transcriptPanel = document.getElementById('transcript-panel');
const liveBoard       = document.getElementById('live-board');
const suggestionsFeed = document.getElementById('suggestions-feed');
const suggestionCountEl = document.getElementById('suggestion-count');
const transcriptWordCountEl = document.getElementById('transcript-word-count');
const historyPanel    = document.getElementById('history-panel');
const historyList     = document.getElementById('history-list');
const scorecardPanel  = document.getElementById('scorecard-panel');
const scorecardContent = document.getElementById('scorecard-content');
const coachToast      = document.getElementById('coach-toast');
const toastText       = document.getElementById('toast-text');
const sessionTimerEl  = document.getElementById('session-timer');
const videoWrapper    = document.querySelector('.video-wrapper');
const coachPersonaSelect = document.getElementById('coach-persona');
const coachModeSelect = document.getElementById('coach-mode');
const deliveryContextSelect = document.getElementById('delivery-context');
const primaryGoalSelect = document.getElementById('primary-goal');
const deckIndicator = document.getElementById('deck-indicator');
const deckSlideTitle = document.getElementById('deck-slide-title');
const deckSlideBody = document.getElementById('deck-slide-body');
const deckSlideImage = document.getElementById('deck-slide-image');
const deckSlidePlaceholder = document.getElementById('deck-slide-placeholder');
const deckPrevBtn = document.getElementById('deck-prev');
const deckNextBtn = document.getElementById('deck-next');
const slideUploadInput = document.getElementById('slide-upload');
const slideUploadBtn = document.getElementById('slide-upload-btn');
const slideUploadStatus = document.getElementById('slide-upload-status');
const screenShareToggle = document.getElementById('screen-share-toggle');
const demoModeToggle = document.getElementById('demo-mode-toggle');

if (coachPersonaSelect) {
  const savedPersona = window.localStorage.getItem('pitchmirror_coach_persona');
  if (savedPersona) coachPersonaSelect.value = savedPersona;
  coachPersonaSelect.addEventListener('change', () => {
    window.localStorage.setItem('pitchmirror_coach_persona', coachPersonaSelect.value);
  });
}

if (coachModeSelect) {
  const savedMode = window.localStorage.getItem('pitchmirror_coach_mode');
  if (savedMode) coachModeSelect.value = savedMode;
  coachModeSelect.addEventListener('change', () => {
    window.localStorage.setItem('pitchmirror_coach_mode', coachModeSelect.value);
  });
}

if (deliveryContextSelect) {
  const savedContext = window.localStorage.getItem('pitchmirror_delivery_context');
  if (savedContext) deliveryContextSelect.value = savedContext;
  deliveryContextSelect.addEventListener('change', () => {
    window.localStorage.setItem('pitchmirror_delivery_context', deliveryContextSelect.value);
  });
}

if (primaryGoalSelect) {
  const savedGoal = window.localStorage.getItem('pitchmirror_primary_goal');
  if (savedGoal) primaryGoalSelect.value = savedGoal;
  primaryGoalSelect.addEventListener('change', () => {
    window.localStorage.setItem('pitchmirror_primary_goal', primaryGoalSelect.value);
  });
}

if (demoModeToggle) {
  const savedDemo = window.localStorage.getItem('pitchmirror_demo_mode');
  if (savedDemo === '1') demoModeToggle.checked = true;
  demoModeToggle.addEventListener('change', () => {
    window.localStorage.setItem('pitchmirror_demo_mode', demoModeToggle.checked ? '1' : '0');
  });
}

if (screenShareToggle) {
  if (!SUPPORTS_SCREEN_SHARE) {
    screenShareToggle.disabled = true;
    screenShareToggle.checked = false;
  } else {
    const savedScreen = window.localStorage.getItem('pitchmirror_screen_share');
    if (savedScreen === '1') screenShareToggle.checked = true;
    screenShareToggle.addEventListener('change', () => {
      window.localStorage.setItem('pitchmirror_screen_share', screenShareToggle.checked ? '1' : '0');
    });
  }
}

function base64ToArrayBuffer(base64) {
  const binary = window.atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

function updateSlideUploadStatus(message, isError = false) {
  if (!slideUploadStatus) return;
  slideUploadStatus.textContent = message;
  slideUploadStatus.style.color = isError ? 'var(--danger)' : 'var(--text-muted)';
}

function currentUploadedSlide() {
  if (!uploadedSlides.length) return null;
  return uploadedSlides[currentUploadedSlideIndex] || null;
}

/** Fetch a single slide by index and cache it. Returns null on failure. */
async function fetchSlide(deckId, idx) {
  if (uploadedSlides[idx]) return uploadedSlides[idx];
  try {
    const resp = await fetch(`/api/slides/${deckId}/${idx}`, { headers: authHeaders() });
    if (!resp.ok) return null;
    const slide = await resp.json();
    uploadedSlides[idx] = slide;
    return slide;
  } catch {
    return null;
  }
}

/**
 * Pre-fetch remaining slides in the background after upload.
 * Yields between each request so the event loop stays responsive.
 */
async function prefetchSlidesBackground(deckId, total) {
  for (let i = 1; i < total; i++) {
    if (uploadedSlides[i]) continue;
    const slide = await fetchSlide(deckId, i);
    // If the user navigated to this slide while we were fetching, refresh the view.
    if (slide && currentUploadedSlideIndex === i) {
      renderPracticeSlide();
      if (sessionActive) sendCurrentUploadedSlideFrame();
    }
    await new Promise(r => setTimeout(r, 30));  // yield to event loop
  }
}

/**
 * If the current slide isn't loaded yet, kick off an async fetch and
 * refresh the view once it arrives.  No-op when slide is already cached.
 */
function maybeLoadCurrentSlide() {
  const idx = currentUploadedSlideIndex;
  if (uploadedSlides[idx] || !uploadedDeckId) return;
  fetchSlide(uploadedDeckId, idx).then(slide => {
    if (slide && currentUploadedSlideIndex === idx) {
      renderPracticeSlide();
      if (sessionActive) sendCurrentUploadedSlideFrame();
    }
  });
}

function sendSlideMeta() {
  if (!sessionActive || !ws || ws.readyState !== WebSocket.OPEN) return;
  if (!uploadedSlides.length) return;
  ws.send(JSON.stringify({
    type: 'slide_index',
    deck_id: uploadedDeckId || '',
    current_slide_index: currentUploadedSlideIndex,
    total_slides: uploadedSlides.length,
  }));
}

function sendCurrentUploadedSlideFrame() {
  if (!sessionActive || !ws || ws.readyState !== WebSocket.OPEN) return;
  const slide = currentUploadedSlide();
  if (!slide || !slide.data_base64) return;
  try {
    sendSlideMeta();
    const buf = base64ToArrayBuffer(slide.data_base64);
    ws.send(makeFrame(T_SLIDE_IN, buf));
  } catch (err) {
    console.warn('failed to send slide frame', err);
  }
}

function renderPracticeSlide() {
  if (uploadedSlides.length) {
    const total = uploadedSlides.length;
    const slide = currentUploadedSlide();
    if (deckIndicator) deckIndicator.textContent = `${currentUploadedSlideIndex + 1}/${total}`;
    if (deckSlideImage) {
      if (slide?.data_base64) {
        deckSlideImage.src = `data:${slide.mime_type || 'image/jpeg'};base64,${slide.data_base64}`;
        deckSlideImage.classList.remove('hidden');
      } else {
        deckSlideImage.src = '';
        deckSlideImage.classList.add('hidden');
      }
    }
    if (deckSlideTitle) deckSlideTitle.classList.add('hidden');
    if (deckSlideBody) deckSlideBody.classList.add('hidden');
    if (deckSlidePlaceholder) {
      deckSlidePlaceholder.textContent = slide?.data_base64 ? 'Uploaded slide deck active.' : 'Loading slide\u2026';
      deckSlidePlaceholder.classList.remove('hidden');
    }
    updateSlideUploadStatus(`Uploaded deck: ${total} slide${total === 1 ? '' : 's'}`);
    return;
  }

  const total = practiceSlides.length;
  const slide = practiceSlides[currentSlideIndex] || practiceSlides[0];
  if (deckIndicator) deckIndicator.textContent = `${currentSlideIndex + 1}/${total}`;
  if (deckSlideTitle) {
    deckSlideTitle.textContent = slide.title;
    deckSlideTitle.classList.remove('hidden');
  }
  if (deckSlideBody) {
    deckSlideBody.textContent = slide.body;
    deckSlideBody.classList.remove('hidden');
  }
  if (deckSlideImage) {
    deckSlideImage.src = '';
    deckSlideImage.classList.add('hidden');
  }
  if (deckSlidePlaceholder) {
    deckSlidePlaceholder.textContent = 'Upload a PDF to enable real slide-aware coaching.';
    deckSlidePlaceholder.classList.remove('hidden');
  }
}

async function uploadSlidesPdf(file) {
  const form = new FormData();
  form.append('file', file);
  updateSlideUploadStatus('Uploading slides...');
  if (slideUploadBtn) slideUploadBtn.disabled = true;
  try {
    const resp = await fetch('/api/slides/upload', {
      method: 'POST',
      headers: authHeaders(),
      body: form,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `Upload failed (${resp.status})`);
    }
    const payload = await resp.json();
    uploadedDeckId = payload.deck_id || '';
    const total = Number(payload.total_slides) || 0;
    // Initialise with null placeholders; slides are fetched lazily/in background.
    uploadedSlides = new Array(total).fill(null);
    // Seed the first slide from the eager upload response (avoids an extra round-trip).
    if (Array.isArray(payload.slides) && payload.slides[0]) {
      uploadedSlides[0] = payload.slides[0];
    }
    currentUploadedSlideIndex = Math.max(0, Number(payload.current_slide_index || 0));
    renderPracticeSlide();
    addTranscriptLine('coach', `Slides uploaded (${total}). I can now critique slide design.`);
    if (sessionActive) sendCurrentUploadedSlideFrame();
    // Pre-fetch remaining slides in the background (non-blocking).
    if (total > 1) prefetchSlidesBackground(uploadedDeckId, total);
  } catch (err) {
    console.error('slide upload failed', err);
    const message = String(err?.message || '');
    if (message.toLowerCase().includes('unauthorized')) {
      updateSlideUploadStatus('Unauthorized: set pitchmirror_api_token in browser localStorage, then reload.', true);
    } else {
      updateSlideUploadStatus(message || 'Slide upload failed.', true);
    }
  } finally {
    if (slideUploadBtn) slideUploadBtn.disabled = false;
  }
}

function navigatePracticeSlides(action) {
  const norm = String(action || '').toLowerCase();
  if (!['next', 'previous', 'first', 'last'].includes(norm)) return;

  if (uploadedSlides.length) {
    const total = uploadedSlides.length;
    if (norm === 'next') currentUploadedSlideIndex = Math.min(total - 1, currentUploadedSlideIndex + 1);
    else if (norm === 'previous') currentUploadedSlideIndex = Math.max(0, currentUploadedSlideIndex - 1);
    else if (norm === 'first') currentUploadedSlideIndex = 0;
    else if (norm === 'last') currentUploadedSlideIndex = total - 1;
    renderPracticeSlide();
    sendCurrentUploadedSlideFrame();
    maybeLoadCurrentSlide();
    return;
  }

  const total = practiceSlides.length;
  if (total === 0) return;
  if (norm === 'next') currentSlideIndex = Math.min(total - 1, currentSlideIndex + 1);
  else if (norm === 'previous') currentSlideIndex = Math.max(0, currentSlideIndex - 1);
  else if (norm === 'first') currentSlideIndex = 0;
  else if (norm === 'last') currentSlideIndex = total - 1;
  renderPracticeSlide();
}

if (slideUploadBtn) {
  slideUploadBtn.addEventListener('click', async () => {
    const file = slideUploadInput?.files?.[0];
    if (!file) {
      updateSlideUploadStatus('Select a PDF before uploading.', true);
      return;
    }
    await uploadSlidesPdf(file);
  });
}

if (slideUploadInput) {
  slideUploadInput.addEventListener('change', () => {
    if (slideUploadInput.files?.[0]) {
      updateSlideUploadStatus(`Selected: ${slideUploadInput.files[0].name}`);
    }
  });
}

if (deckPrevBtn) {
  deckPrevBtn.addEventListener('click', () => navigatePracticeSlides('previous'));
}
if (deckNextBtn) {
  deckNextBtn.addEventListener('click', () => navigatePracticeSlides('next'));
}
renderPracticeSlide();

/* ── Status helper ───────────────────────────────── */
function setStatus(state, label) {
  statusBadge.className = `status-badge ${state}`;
  statusBadge.textContent = label || state.charAt(0).toUpperCase() + state.slice(1);
}

/* ── Session timer ───────────────────────────────── */
function startTimer() {
  sessionStartTime = Date.now();
  if (sessionTimerEl) sessionTimerEl.classList.remove('hidden');
  timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - sessionStartTime) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    if (sessionTimerEl) sessionTimerEl.textContent = `${m}:${s}`;
  }, 1000);
}

function stopTimer() {
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  if (sessionTimerEl) sessionTimerEl.classList.add('hidden');
}

/* ── Session start ───────────────────────────────── */
btnStart.addEventListener('click', async () => {
  try {
    await startSession();
  } catch (err) {
    console.error('Failed to start session:', err);
    setStatus('error', 'Camera/mic error');
    alert(`Could not access camera or microphone: ${err.message}`);
  }
});

btnStop.addEventListener('click', stopSession);

async function startSession() {
  const sessionCfg = {
    persona: coachPersonaSelect?.value || 'coach',
    mode: coachModeSelect?.value || 'general',
    deliveryContext: deliveryContextSelect?.value || 'virtual',
    primaryGoal: primaryGoalSelect?.value || 'balanced',
    screenEnabled: !!screenShareToggle?.checked,
    demoMode: !!demoModeToggle?.checked,
  };

  // 1. Get user media
  localStream = await navigator.mediaDevices.getUserMedia({
    video: { width: VIDEO_WIDTH, height: VIDEO_HEIGHT, facingMode: 'user' },
    audio: { ...window.PMAudio.MIC_CONSTRAINTS, sampleRate: AUDIO_SAMPLE_RATE },
  });

  video.srcObject = localStream;
  await video.play();

  if (sessionCfg.screenEnabled) {
    await startScreenStream();
    sessionCfg.screenEnabled = !!screenStream;
  }

  // Activate broadcast corner brackets
  if (videoWrapper) videoWrapper.classList.add('recording');

  // 2. Setup video canvas for frame capture
  videoCanvas = document.createElement('canvas');
  videoCanvas.width = VIDEO_WIDTH;
  videoCanvas.height = VIDEO_HEIGHT;

  // 3. Connect WebSocket
  ws = new WebSocket(buildWsUrl(sessionCfg));
  ws.binaryType = 'arraybuffer';   // receive binary frames as ArrayBuffer (not Blob)

  // 4. Setup overlay canvas for real-time AI drawing
  if (overlayCanvas) {
    overlayCtx = overlayCanvas.getContext('2d');
    overlayCanvas.width = video.videoWidth || 640;
    overlayCanvas.height = video.videoHeight || 480;
    clearOverlay();
  }

  ws.onopen = () => {
    console.log('WebSocket connected');
    sessionActive = true;
    window.__pmSessionActive = true;
    startAudioCapture();
    startVideoCapture();
    startGazeTracking(sessionCfg.deliveryContext);
    if (sessionCfg.screenEnabled) startScreenCapture();
    if (uploadedSlides.length) sendCurrentUploadedSlideFrame();
    startTimer();
  };

  ws.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      // Binary frame from server — only coach audio (0x10) currently
      const view = new Uint8Array(event.data);
      if (view[0] === T_AUDIO_OUT) {
        playCoachAudio(event.data.slice(1));
      }
    } else {
      // JSON text frame — all control messages
      handleServerMessage(event);
    }
  };

  ws.onerror = (e) => {
    console.error('WebSocket error', e);
    setStatus('error', 'Connection lost');
  };

  ws.onclose = () => {
    if (sessionActive) {
      // Server closed mid-session (rate limit, error, timeout).
      stopSession();
      setStatus('error', 'Disconnected');
    } else if (_waitingForScorecard && _currentSessionId) {
      // WS closed during the analysis phase (network drop, Cloud Run restart, etc.)
      // The backend still completes analysis and saves to Firestore — poll for it.
      const recoverSessionId = _currentSessionId;
      _waitingForScorecard = false;
      setStatus('analyzing', 'Reconnecting to results\u2026');
      setTimeout(() => tryRecoverScorecard(recoverSessionId), 2500);
    }
  };

  // 4. UI updates
  btnStart.classList.add('hidden');
  btnStop.classList.remove('hidden');
  if (coachPersonaSelect) coachPersonaSelect.disabled = true;
  if (coachModeSelect) coachModeSelect.disabled = true;
  if (deliveryContextSelect) deliveryContextSelect.disabled = true;
  if (primaryGoalSelect) primaryGoalSelect.disabled = true;
  if (screenShareToggle) screenShareToggle.disabled = true;
  if (demoModeToggle) demoModeToggle.disabled = true;
  const historyBtn = document.getElementById('btn-history');
  if (historyBtn) historyBtn.disabled = true;
  liveMetrics.classList.remove('hidden');
  if (_agentTelemetry) { _agentTelemetry.classList.remove('hidden'); _telemetryHistory.length = 0; }
  videoOverlay.classList.remove('hidden');
  recIndicator.classList.remove('hidden');
  transcriptFeed.innerHTML = '';
  resetSuggestions();
  if (transcriptWordCountEl) transcriptWordCountEl.textContent = '0 words';
  historyPanel?.classList.add('hidden');
  _activeLine = { speaker: null, el: null };
  _lastCoachLine = { normalized: '', at: 0 };
  _lastUserLine  = { normalized: '', at: 0 };
  _currentSessionId = null;
  _waitingForScorecard = false;
  _replayTimeline = { markers: [], duration: 0 };
  window._analysisReport = null;
  window._researchTips   = null;
  window._generatedAssets = null;
  scorecardPanel.classList.add('hidden');
  document.querySelector('.right-panel')?.classList.remove('results-mode');
  document.getElementById('analysis-panel')?.classList.add('hidden');
  liveBoard?.classList.remove('hidden');
  setStatus('connected', `Connecting (${sessionCfg.mode.replace('_', ' ')})...`);
}

/* ── Audio capture (PCM 16kHz) ───────────────────── */
async function startAudioCapture() {
  audioContext = new AudioContext({ sampleRate: AUDIO_SAMPLE_RATE });
  // Mic → high-pass → leveller (quiet speech boosted, loud speech held back) → server.
  audioWorklet = await window.PMAudio.createLeveler(audioContext, localStream, (chunk) => {
    if (!sessionActive || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (Date.now() < _cueSpeakingUntil) return;   // don't transcribe the coach's spoken cue
    // Convert float32 → int16 PCM, send as binary frame — no JSON, no base64
    const pcm16 = float32ToInt16(chunk.pcm);
    ws.send(makeFrame(T_AUDIO_IN, pcm16.buffer));
  }, { kind: 'mic', chunkSamples: Math.floor(AUDIO_SAMPLE_RATE * AUDIO_CHUNK_MS / 1000) });
}

function float32ToInt16(float32Array) {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return int16;
}

/* ── Video capture (JPEG at 1fps) ────────────────── */
function startVideoCapture() {
  videoInterval = setInterval(() => {
    if (!sessionActive || !ws || ws.readyState !== WebSocket.OPEN) return;
    const ctx = videoCanvas.getContext('2d');
    ctx.save();
    ctx.scale(-1, 1);  // mirror to match the display
    ctx.drawImage(video, -VIDEO_WIDTH, 0, VIDEO_WIDTH, VIDEO_HEIGHT);
    ctx.restore();
    videoCanvas.toBlob((blob) => {
      if (!blob) return;
      blob.arrayBuffer().then((buf) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(makeFrame(T_VIDEO_IN, buf));
        }
      });
    }, 'image/jpeg', VIDEO_QUALITY);
  }, 1000 / VIDEO_FPS);
}

/* ── Gaze tracking (in-browser face landmarks) ───── */
// Reports camera / away / notes / no_face to the server, which times look-away
// streaks for the scorecard. Also drives the eye-contact badge on the video.
const GAZE_LABELS = { camera: 'Camera OK', away: 'Looking away', notes: 'Looking down', no_face: 'No face' };
const GAZE_WARN_AFTER_S = { virtual: 5, hybrid: 6.5, in_person: 8 };
let _gazeState = 'camera';
let _gazeSince = 0;
let _gazeBadgeTimer = null;

function _gazeDisengaged(state, context) {
  return state === 'notes' || state === 'no_face' || (state === 'away' && context === 'virtual');
}

function _renderGazeBadge(context) {
  const badge = document.getElementById('eye-indicator');
  const label = document.getElementById('eye-label');
  if (!badge || !label) return;
  const secs = (performance.now() - _gazeSince) / 1000;
  const disengaged = _gazeDisengaged(_gazeState, context);
  const limit = GAZE_WARN_AFTER_S[context] || 5;
  badge.classList.remove('gaze-warn', 'gaze-bad', 'gaze-off');
  if (disengaged) badge.classList.add(secs >= limit ? 'gaze-bad' : 'gaze-warn');
  label.textContent = disengaged && secs >= 1
    ? `${GAZE_LABELS[_gazeState]} ${Math.floor(secs)}s`
    : (GAZE_LABELS[_gazeState] || 'Camera OK');
}

function startGazeTracking(context) {
  if (!window.GazeTracker) return;
  _gazeState = 'camera';
  _gazeSince = performance.now();
  const label = document.getElementById('eye-label');
  if (label) label.textContent = 'Starting gaze tracking…';
  window.GazeTracker.start(video, (state) => {
    if (state !== _gazeState) { _gazeState = state; _gazeSince = performance.now(); }
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'gaze', state }));
    }
  }).then((ok) => {
    if (!ok) {
      const badge = document.getElementById('eye-indicator');
      if (badge) badge.classList.add('gaze-off');
      if (label) label.textContent = 'Gaze tracking unavailable';
      return;
    }
    _gazeBadgeTimer = setInterval(() => _renderGazeBadge(context), 500);
  });
}

function stopGazeTracking() {
  if (window.GazeTracker) window.GazeTracker.stop();
  if (_gazeBadgeTimer) { clearInterval(_gazeBadgeTimer); _gazeBadgeTimer = null; }
  const badge = document.getElementById('eye-indicator');
  const label = document.getElementById('eye-label');
  if (badge) badge.classList.remove('gaze-warn', 'gaze-bad', 'gaze-off');
  if (label) label.textContent = 'Camera OK';
}

// Warm the face model while the user fills in the intake form.
window.GazeTracker?.preload();

/* ── Screen-share capture (optional) ─────────────── */
async function startScreenStream() {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    console.warn('Screen capture API not available in this browser.');
    return;
  }

  try {
    screenStream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 5 },
      audio: false,
    });
  } catch (err) {
    console.warn('Screen-share not granted:', err);
    if (screenShareToggle) screenShareToggle.checked = false;
    return;
  }

  screenVideo = document.createElement('video');
  screenVideo.srcObject = screenStream;
  screenVideo.muted = true;
  screenVideo.playsInline = true;
  await screenVideo.play();

  const track = screenStream.getVideoTracks()[0];
  if (track) {
    track.addEventListener('ended', () => {
      stopScreenCapture();
      if (screenShareToggle) screenShareToggle.checked = false;
      if (sessionActive) addTranscriptLine('coach', 'Screen share ended. Continuing webcam-only coaching.');
    });
  }

  screenCanvas = document.createElement('canvas');
}

function startScreenCapture() {
  if (!screenStream || !screenVideo || !screenCanvas) return;

  screenInterval = setInterval(() => {
    if (!sessionActive || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (!screenVideo.videoWidth || !screenVideo.videoHeight) return;

    const maxW = 960;
    const aspect = screenVideo.videoWidth / screenVideo.videoHeight;
    const width = Math.min(maxW, screenVideo.videoWidth);
    const height = Math.round(width / aspect);
    screenCanvas.width = width;
    screenCanvas.height = height;

    const ctx = screenCanvas.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(screenVideo, 0, 0, width, height);

    screenCanvas.toBlob((blob) => {
      if (!blob) return;
      blob.arrayBuffer().then((buf) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(makeFrame(T_SCREEN_IN, buf));
        }
      });
    }, 'image/jpeg', SCREEN_QUALITY);
  }, 1000 / SCREEN_FPS);
}

function stopScreenCapture() {
  if (screenInterval) {
    clearInterval(screenInterval);
    screenInterval = null;
  }
  if (screenStream) {
    screenStream.getTracks().forEach((t) => t.stop());
    screenStream = null;
  }
  if (screenVideo) {
    screenVideo.srcObject = null;
    screenVideo = null;
  }
  screenCanvas = null;
}

/* ── Post-session scorecard recovery ─────────────── */
/**
 * Poll /api/sessions/{sessionId} until the scorecard is available.
 * Used when the WebSocket closes during analysis (network drop, server restart).
 * The backend always finishes analysis and saves to Firestore even if the WS is gone.
 */
async function tryRecoverScorecard(sessionId) {
  const MAX_ATTEMPTS = 8;
  const BASE_DELAY_MS = 3000;
  for (let i = 0; i < MAX_ATTEMPTS; i++) {
    await new Promise(r => setTimeout(r, BASE_DELAY_MS + i * 2000));
    try {
      const resp = await fetch(`/api/sessions/${sessionId}`, { headers: authHeaders() });
      if (resp.ok) {
        const data = await resp.json();
        // A completed scorecard has at least an overall_score
        if (data && (data.overall_score !== undefined || data.final_report)) {
          renderScorecard(data);
          loadRecentSessions();
          return;
        }
      }
    } catch { /* network still down — keep retrying */ }
  }
  // Gave up — surface an error so the user isn't left with a spinning panel
  document.getElementById('analysis-panel')?.classList.add('hidden');
  setStatus('error', 'Results unavailable — check session history');
}

/* ── Session stop ────────────────────────────────── */
function stopSession() {
  sessionActive = false;
  window.__pmSessionActive = false;

  stopTimer();
  if (videoWrapper) videoWrapper.classList.remove('recording');

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(new Uint8Array([T_STOP]).buffer);
  }

  if (videoInterval) { clearInterval(videoInterval); videoInterval = null; }
  stopGazeTracking();
  stopScreenCapture();
  if (audioWorklet)  { audioWorklet.port.onmessage = null; audioWorklet.pmSource?.disconnect(); audioWorklet.disconnect(); audioWorklet = null; }
  if (audioWorkletSink) { audioWorkletSink.disconnect(); audioWorkletSink = null; }
  if (audioContext)  { audioContext.close(); audioContext = null; }
  if (coachAudioContext) {
    coachAudioContext.close().catch(() => {});
    coachAudioContext = null;
  }
  coachNextPlayAt = 0;
  if (localStream)   { localStream.getTracks().forEach(t => t.stop()); localStream = null; }

  video.srcObject = null;
  btnStop.classList.add('hidden');
  btnStart.classList.remove('hidden');
  if (coachPersonaSelect) coachPersonaSelect.disabled = false;
  if (coachModeSelect) coachModeSelect.disabled = false;
  if (deliveryContextSelect) deliveryContextSelect.disabled = false;
  if (primaryGoalSelect) primaryGoalSelect.disabled = false;
  if (screenShareToggle) screenShareToggle.disabled = !SUPPORTS_SCREEN_SHARE;
  if (demoModeToggle) demoModeToggle.disabled = false;
  const historyBtn = document.getElementById('btn-history');
  if (historyBtn) historyBtn.disabled = false;
  recIndicator.classList.add('hidden');
  setStatus('idle', 'Session ended');
}

/* ── Spoken cue fallback (browser voice) ─────────── */
let _lastCoachAudioAt = 0;
let _cueSpeakingUntil = 0;   // mic chunks are not sent while a cue plays (avoids echo in the transcript)

function speakCue(text) {
  showCoachToast(text);
  addTranscriptLine('coach', text);
  if (!('speechSynthesis' in window)) return;
  if (Date.now() - _lastCoachAudioAt < 3000) return;   // the AI coach is already talking
  const u = new SpeechSynthesisUtterance(text);
  u.lang = 'en-US';
  u.rate = 1.05;
  const voice = window.speechSynthesis.getVoices().find((v) => /en[-_](US|GB|IN)/i.test(v.lang) && /female|samantha|google/i.test(v.name))
    || window.speechSynthesis.getVoices().find((v) => /^en/i.test(v.lang));
  if (voice) u.voice = voice;
  _cueSpeakingUntil = Date.now() + 6000;
  u.onend = u.onerror = () => { _cueSpeakingUntil = Date.now() + 300; };
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(u);
}

/* ── Coach audio playback (PCM 24kHz) ───────────── */
function playCoachAudio(pcmArrayBuffer) {
  _lastCoachAudioAt = Date.now();
  if (!coachAudioContext) {
    coachAudioContext = new AudioContext({ sampleRate: COACH_SAMPLE_RATE });
    coachNextPlayAt = 0;
  } else if (coachAudioContext.state === 'suspended') {
    coachAudioContext.resume().catch(() => {});
  }

  // Raw 16-bit PCM little-endian → float32 — no base64 decode needed
  const view = new DataView(pcmArrayBuffer);
  const samples = new Float32Array(pcmArrayBuffer.byteLength / 2);
  for (let i = 0; i < samples.length; i++) {
    samples[i] = view.getInt16(i * 2, true) / 32768;  // little-endian
  }
  if (!samples.length) return;

  const buffer = coachAudioContext.createBuffer(1, samples.length, COACH_SAMPLE_RATE);
  buffer.getChannelData(0).set(samples);

  const source = coachAudioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(coachAudioContext.destination);

  // Queue audio chunks to avoid overlap/echo artifacts from immediate start().
  const now = coachAudioContext.currentTime;
  if (coachNextPlayAt < now - COACH_AUDIO_RESET_GAP_S) {
    coachNextPlayAt = now;
  }
  // If queue gets too far ahead (network jitter/backpressure), trim latency.
  if (coachNextPlayAt > now + COACH_AUDIO_MAX_BUFFER_AHEAD_S) {
    coachNextPlayAt = now + COACH_AUDIO_SCHEDULE_LEAD_S;
  }
  const startAt = Math.max(now + COACH_AUDIO_SCHEDULE_LEAD_S, coachNextPlayAt);
  source.start(startAt);
  coachNextPlayAt = startAt + buffer.duration;
  source.onended = () => {
    try { source.disconnect(); } catch (_) {}
  };
}

/* ── Server message handler ──────────────────────── */
function handleServerMessage(event) {
  const msg = JSON.parse(event.data);

  switch (msg.type) {

    case 'status':
      handleStatus(msg.state, msg.message);
      if (msg.state === 'connected' && msg.session_id) {
        _currentSessionId = msg.session_id;
      }
      if (msg.state === 'connected') {
        if (msg.coach_mode && coachModeSelect) {
          coachModeSelect.value = msg.coach_mode;
        }
        if (msg.delivery_context && deliveryContextSelect) {
          deliveryContextSelect.value = msg.delivery_context;
        }
        if (msg.primary_goal && primaryGoalSelect) {
          primaryGoalSelect.value = msg.primary_goal;
        }
        if (msg.screen_enabled === false && screenShareToggle?.checked) {
          addTranscriptLine('coach', 'Server disabled screen-aware mode for this deployment.');
        }
        if (Number(msg.total_slides) > 0 && uploadedSlides.length === 0) {
          updateSlideUploadStatus(`Server reports ${msg.total_slides} cached slides. Upload PDF in this tab to display them.`);
        }
      }
      break;

    case 'transcript':
      addTranscriptLine(msg.speaker, msg.text, !!msg.raw);
      break;

    case 'transcript_end':
      // End of one spoken utterance — the next words start a new transcript line.
      if (_activeLine.speaker === String(msg.speaker || '').toLowerCase()) {
        _activeLine = { speaker: null, el: null };
      }
      break;

    case 'coach_tip':
      addSuggestionCard(msg);
      break;

    case 'coach_cue':
      setSuggestionSaid(msg.index, msg.text, msg.silent ? 'quiet' : 'cue');
      break;

    case 'coach_said':
      setSuggestionSaid(msg.index, msg.text, 'said');
      break;

    case 'metric':
      updateMetric(msg.key, msg.value);
      break;

    case 'coach_cue':
      // The server asks the browser to voice a short correction when the AI coach
      // flagged an issue but didn't speak.
      if (msg.speak && msg.text) speakCue(msg.text);
      break;

    case 'tool_call':
      // ADK tool invocation — show as a visible agent action in the transcript
      if (msg.tool === 'draw_overlay') {
        drawOverlayHighlight(msg.args.x, msg.args.y, msg.args.label);
      } else if (msg.tool === 'navigate_practice_slides') {
        // Uploaded decks are controlled by backend slide_change events to avoid
        // double-advancing and duplicate frame sends.
        if (!uploadedSlides.length) {
          navigatePracticeSlides(msg.args?.action || 'next');
        }
      }
      showToolCallEvent(msg.tool, msg.args);
      updateTelemetry(msg.tool, msg.args);
      break;

    case 'telemetry':
      updateTelemetryEvent(msg);
      break;

    case 'demo_seed':
      showDemoSeedEvent(msg);
      updateTelemetryEvent({
        phase: 'demo_checkpoint',
        detail: msg.seed || 'checkpoint',
        data: { message: msg.message || '' },
      });
      break;

    case 'slide_mark':
      showSlideMarkEvent(msg);
      break;

    case 'live_visual_hint':
      handleLiveVisualHint(msg);
      break;

    case 'slide_change':
      if (uploadedSlides.length) {
        const idx = Number(msg.current_slide_index);
        if (Number.isFinite(idx)) {
          currentUploadedSlideIndex = Math.min(
            Math.max(0, idx),
            Math.max(0, uploadedSlides.length - 1),
          );
          renderPracticeSlide();
          sendCurrentUploadedSlideFrame();
          maybeLoadCurrentSlide();
        }
      } else {
        navigatePracticeSlides(msg.action || 'next');
      }
      break;

    case 'pipeline_step': {
      // Real step completion event from backend — no fake timers needed
      const stepMap = {
        delivery_done:  'step-delivery',
        content_done:   'step-content',
        research_done:  'step-research',
        synthesis_done: 'step-synthesis',
        memory_updated: 'step-memory',
      };
      const stepId = stepMap[msg.step];
      if (stepId) markPipelineStepDone(stepId);
      if (msg.step === 'visuals_start') {
        addTranscriptLine('coach', 'Generating visual improvement cards...');
      }
      break;
    }

    case 'analysis_complete': {
      // Post-session pipeline finished — store report + research for scorecard
      window._analysisReport = msg.report;
      window._researchTips   = msg.research_tips || '';
      window._generatedAssets = msg.generated_assets || [];
      window._agentsUsed = msg.agents_used || [];
      // Ensure all steps are visually done (fallback for any missed pipeline_step events)
      ['step-delivery', 'step-content', 'step-research', 'step-synthesis', 'step-memory'].forEach(markPipelineStepDone);
      setTimeout(() => document.getElementById('analysis-panel').classList.add('hidden'), 900);
      break;
    }

    case 'scorecard':
      _waitingForScorecard = false;
      renderScorecard(msg.data);
      loadRecentSessions();
      break;
  }
}

function handleStatus(state, message) {
  const labels = {
    connected:    'Connecting...',
    listening:    'Listening',
    coaching:     'Coach Speaking',
    reconnecting: 'Reconnecting...',
    analyzing:    'Analyzing Session...',
    error:        message || 'Error',
  };
  setStatus(state, labels[state] || state);

  if (state === 'analyzing') {
    _waitingForScorecard = true;
    liveMetrics.classList.add('hidden');
    if (_agentTelemetry) _agentTelemetry.classList.add('hidden');
    recIndicator.classList.add('hidden');
    liveBoard?.classList.add('hidden');
    document.getElementById('analysis-panel').classList.remove('hidden');

    // Reset all steps to spinning — real completion events drive the done state
    ['step-delivery', 'step-content', 'step-research', 'step-synthesis', 'step-memory'].forEach(id => {
      const step = document.getElementById(id);
      if (!step) return;
      step.classList.remove('done');
      const dot = step.querySelector('.step-dot');
      if (dot) { dot.classList.remove('done'); dot.classList.add('spinning'); }
    });
  }
}

/* ── Transcript ──────────────────────────────────── */
function normalizeTranscriptForDedup(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function sanitizeTranscriptText(value) {
  return String(value || '')
    .replace(/<ctrl\d+>/gi, ' ')
    .replace(/<(spoken_[^>]*|noise|inaudible|unk|unknown)>/gi, ' ')
    .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function isRecentCoachDuplicate(text) {
  const normalized = normalizeTranscriptForDedup(text);
  if (!normalized) return true;
  if (!_lastCoachLine.normalized) return false;
  const elapsed = Date.now() - _lastCoachLine.at;
  if (elapsed > COACH_DEDUP_WINDOW_MS) return false;
  const last = _lastCoachLine.normalized;
  // Exact match: same full text re-delivered (e.g. output_transcription re-sent).
  // Prefix match: first streaming chunk of a duplicate turn won't match the full
  // stored line exactly — check if the incoming text is the start of what we
  // already showed (guards against "Monitor your pace" not matching the full
  // remembered "monitor your pace you re speaking quite quickly").
  return last === normalized || last.startsWith(normalized);
}

function rememberCoachLine(text) {
  const normalized = normalizeTranscriptForDedup(text);
  if (!normalized) return;
  _lastCoachLine = { normalized, at: Date.now() };
}

function isRecentUserDuplicate(text) {
  const normalized = normalizeTranscriptForDedup(text);
  if (!normalized) return true;
  if (!_lastUserLine.normalized) return false;
  const elapsed = Date.now() - _lastUserLine.at;
  return elapsed <= USER_DEDUP_WINDOW_MS && normalized === _lastUserLine.normalized;
}

function rememberUserLine(text) {
  const normalized = normalizeTranscriptForDedup(text);
  if (!normalized) return;
  _lastUserLine = { normalized, at: Date.now() };
}

function coachChunkAlreadyRendered(existingText, incomingText) {
  const existingNorm = normalizeTranscriptForDedup(existingText);
  const incomingNorm = normalizeTranscriptForDedup(incomingText);
  if (!incomingNorm) return true;
  if (!existingNorm) return false;
  if (existingNorm === incomingNorm) return true;
  if (incomingNorm.length >= 12 && existingNorm.includes(incomingNorm)) return true;
  return false;
}

function _sessionElapsedLabel() {
  if (!sessionStartTime) return '';
  return formatTime(Math.max(0, (Date.now() - sessionStartTime) / 1000));
}

/** Clean a streaming transcription chunk but keep its edge spaces (they mark word boundaries). */
function cleanTranscriptChunk(value) {
  return String(value || '')
    .replace(/<ctrl\d+>/gi, ' ')
    .replace(/<(spoken_[^>]*|noise|inaudible|unk|unknown)>/gi, ' ')
    .replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, ' ')
    .replace(/\s+/g, ' ');
}

let _wordCountFrame = 0;
function scheduleWordCountUpdate() {
  if (!transcriptWordCountEl || _wordCountFrame) return;
  _wordCountFrame = requestAnimationFrame(() => {
    _wordCountFrame = 0;
    let words = 0;
    transcriptFeed.querySelectorAll('.transcript-line.user .transcript-text').forEach((el) => {
      const t = el.textContent.trim();
      if (t) words += t.split(/\s+/).length;
    });
    transcriptWordCountEl.textContent = `${words} word${words === 1 ? '' : 's'}`;
  });
}

/**
 * Add speech to the live transcript.
 * raw=true: a streaming sub-word chunk from the server; appended as-is because
 * its own leading/trailing spaces mark word boundaries ("frien" + "dly").
 */
function addTranscriptLine(speaker, text, raw = false) {
  const normalizedSpeaker = String(speaker || '').toLowerCase();
  const chunk = raw ? cleanTranscriptChunk(text) : sanitizeTranscriptText(text);
  if (!chunk) return;
  const sameLine = _activeLine.speaker === normalizedSpeaker && _activeLine.el;
  if (!chunk.trim() && !sameLine) return;

  if (!raw) {
    // Drop repeated coach lines (streaming retries).
    if (normalizedSpeaker === 'coach' && isRecentCoachDuplicate(chunk)) return;
    // Drop repeated user lines (deferred finals re-sent after the model responds).
    if (normalizedSpeaker === 'user' && isRecentUserDuplicate(chunk)) return;
  }

  const welcomePanel = transcriptFeed.querySelector('#welcome-panel');
  if (welcomePanel) welcomePanel.remove();

  let textEl;
  if (sameLine) {
    textEl = _activeLine.el.querySelector('.transcript-text');
    if (!textEl) return;
    if (raw) {
      textEl.textContent = (textEl.textContent + chunk).replace(/\s+/g, ' ');
    } else {
      if (normalizedSpeaker === 'coach' && coachChunkAlreadyRendered(textEl.textContent, chunk)) return;
      const needsSpace = textEl.textContent.length > 0 && !textEl.textContent.endsWith(' ');
      textEl.textContent += (needsSpace ? ' ' : '') + chunk;
    }
  } else {
    // Different speaker (or first line) — start a new transcript entry
    const line = document.createElement('div');
    line.className = `transcript-line ${normalizedSpeaker}`;
    line.innerHTML = `
      <span class="transcript-meta">
        <span class="transcript-speaker">${normalizedSpeaker === 'coach' ? 'Coach' : 'You'}</span>
        <span class="transcript-time">${escapeHtml(_sessionElapsedLabel())}</span>
      </span>
      <span class="transcript-text"></span>
    `;
    textEl = line.querySelector('.transcript-text');
    textEl.textContent = raw ? chunk.trimStart() : chunk;
    transcriptFeed.appendChild(line);
    _activeLine = { speaker: normalizedSpeaker, el: line };
  }

  transcriptFeed.scrollTop = transcriptFeed.scrollHeight;
  const fullText = textEl.textContent.trim();
  if (normalizedSpeaker === 'coach') {
    rememberCoachLine(fullText);
    if (fullText) showCoachToast(fullText);
  } else if (normalizedSpeaker === 'user') {
    rememberUserLine(fullText);
    scheduleWordCountUpdate();
  }
}

/* ── Coach suggestions (live) ────────────────────── */
const ISSUE_LABELS = {
  filler:         'Filler words',
  pace:           'Pace',
  eye_contact:    'Eye contact',
  contradiction:  'Contradiction',
  clarity:        'Clarity',
  slide_clarity:  'Slide clarity',
  slide_mismatch: 'Slide mismatch',
  off_topic:      'Off topic',
  rambling:       'Rambling',
  monotone:       'Flat tone',
  confidence:     'Hedging',
  interruption:   'Interrupted',
};
const ISSUE_TIPS = {
  filler:         'Replace the filler with a silent pause — breathe, then continue.',
  pace:           'Slow down: finish each sentence, then pause for a beat.',
  eye_contact:    'Look into the camera lens when you land a key point.',
  contradiction:  'Pick one claim and restate it clearly — don’t walk it back.',
  clarity:        'Say it again in one short sentence: point first, detail second.',
  slide_clarity:  'Cut the slide to one idea and at most three short bullets.',
  slide_mismatch: 'Talk about what’s on screen, or move to the matching slide.',
  off_topic:      'Answer the exact question first, then add context.',
  rambling:       'Make the point in two sentences, then stop and check in.',
  monotone:       'Lift your energy: stress key words and vary your pitch.',
  confidence:     'Replace “I think / maybe” with a direct statement.',
  interruption:   'Let them finish their sentence, pause one beat, then answer.',
};

function evidenceSummary(ev) {
  if (!ev || !ev.metric) return '';
  const parts = [String(ev.metric).replace(/_/g, ' ')];
  if (ev.measured != null && ev.measured !== '') {
    parts.push(`measured ${ev.measured}${ev.metric === 'seconds_away' ? 's' : ''}`);
  }
  if (ev.threshold != null && ev.threshold !== 'n/a') parts.push(`threshold ${ev.threshold}`);
  else if (ev.threshold_s != null) parts.push(`threshold ≥${ev.threshold_s}s`);
  if (ev.session_total != null) parts.push(`session total ${ev.session_total}`);
  return parts.join(' · ');
}

const SAID_LABELS = {
  said:  'Coach said',
  cue:   'Coach says',
  quiet: 'Coach tip · noted quietly (cooldown)',
};

/** HTML for one coaching-moment card (shared by the live panel and the end-of-session review). */
function suggestionCardHtml(item) {
  const type = String(item.type || 'issue');
  const label = ISSUE_LABELS[type] || type.replace(/_/g, ' ');
  const tip = ISSUE_TIPS[type] || '';
  const ev = evidenceSummary(item.evidence);
  const sensorLabel = item.evidence?.sensor === 'browser_face_tracking' ? 'measured by face tracking'
    : item.evidence?.sensor === 'audio_overlap' ? 'measured from audio'
    : item.evidence?.sensor ? 'measured' : 'measured by face tracking';
  const sensor = item.source === 'sensor' ? `<span class="sg-badge">${sensorLabel}</span>` : '';
  const vote = item.userRating === 'up' ? '<span class="sg-vote">👍 you found this helpful</span>'
    : item.userRating === 'down' ? '<span class="sg-vote">👎 you found this unhelpful</span>' : '';
  const said = (item.said || '').trim();
  return `
    <div class="sg-head">
      <span class="sg-time">${escapeHtml(formatTime(Number(item.ts) || 0))}</span>
      <span class="sg-type type-${escapeHtml(type)}">${escapeHtml(label)}</span>
      ${sensor}
      ${vote}
    </div>
    ${item.description ? `<div class="sg-desc">${escapeHtml(item.description)}</div>` : ''}
    <div class="sg-said ${said ? '' : 'hidden'}">
      <span class="sg-label">${escapeHtml(SAID_LABELS[item.saidKind || 'said'])}</span>
      <span class="sg-said-text">${escapeHtml(said ? `“${said}”` : '')}</span>
    </div>
    ${tip ? `<div class="sg-tip"><span class="sg-label">Try this</span>${escapeHtml(tip)}</div>` : ''}
    ${ev ? `<div class="sg-evidence">${escapeHtml(ev)}</div>` : ''}`;
}

function resetSuggestions() {
  _suggestionCards.clear();
  _pendingCoachSaid.clear();
  _suggestionCount = 0;
  if (suggestionCountEl) suggestionCountEl.textContent = '0';
  if (suggestionsFeed) suggestionsFeed.innerHTML = SUGGESTIONS_EMPTY_HTML;
}

function _prependToSuggestions(el) {
  if (!suggestionsFeed) return;
  suggestionsFeed.querySelector('.suggestions-empty')?.remove();
  suggestionsFeed.prepend(el);   // newest first — always visible without scrolling
  suggestionsFeed.scrollTop = 0;
}

function addSuggestionCard(msg) {
  const index = Number(msg.index);
  const card = document.createElement('div');
  card.className = `suggestion-card type-${msg.issue_type || 'issue'}`;
  const item = {
    type: msg.issue_type,
    ts: msg.ts,
    description: msg.description,
    evidence: msg.evidence,
    source: msg.source,
  };
  const pending = _pendingCoachSaid.get(index);
  if (pending) {
    item.said = pending.text;
    item.saidKind = pending.kind;
    _pendingCoachSaid.delete(index);
  }
  card.innerHTML = suggestionCardHtml(item);
  _suggestionCards.set(index, card);
  _prependToSuggestions(card);
  _suggestionCount += 1;
  if (suggestionCountEl) suggestionCountEl.textContent = String(_suggestionCount);
}

/** kind: 'said' (actual coach speech) | 'cue' (cue requested) | 'quiet' (shown, not spoken). */
function setSuggestionSaid(index, text, kind) {
  const idx = Number(index);
  const clean = String(text || '').trim();
  if (!clean || !Number.isFinite(idx)) return;
  const card = _suggestionCards.get(idx);
  if (!card) {
    const prev = _pendingCoachSaid.get(idx);
    if (!(prev && prev.kind === 'said' && kind !== 'said')) _pendingCoachSaid.set(idx, { text: clean, kind });
    return;
  }
  const box = card.querySelector('.sg-said');
  if (!box) return;
  // Real coach speech always wins over the requested cue.
  if (kind !== 'said' && box.dataset.kind === 'said') return;
  box.dataset.kind = kind;
  box.classList.remove('hidden');
  box.querySelector('.sg-label').textContent = SAID_LABELS[kind] || SAID_LABELS.said;
  box.querySelector('.sg-said-text').textContent = `“${clean}”`;
  if (kind === 'quiet') showCoachToast(clean);
}

/* ── ADK tool call visibility ────────────────────── */
// Measurement/bookkeeping tools are already visible in the Agent Activity bar;
// flag_issue becomes a full coaching card via the "coach_tip" event.
const QUIET_TOOLS = new Set([
  'flag_issue', 'get_speech_metrics', 'get_recent_transcript', 'check_eye_contact', 'draw_overlay',
]);

function showToolCallEvent(tool, args) {
  if (QUIET_TOOLS.has(tool)) return;

  const line = document.createElement('div');
  line.className = 'transcript-line tool-call';

  if (tool === 'flag_issue') {
    const typeLabels = {
      filler:        'Filler words',
      pace:          'Pace',
      eye_contact:   'Eye contact',
      contradiction: 'Contradiction',
      clarity:       'Clarity',
      slide_clarity: 'Slide clarity',
      slide_mismatch:'Slide mismatch',
    };
    const label = typeLabels[args?.issue_type || ''] || args?.issue_type || '';
    // Build a human-readable evidence chip so judges can see exactly what threshold triggered
    const ev = args?.evidence || {};
    let evidenceHtml = '';
    if (ev.metric && ev.threshold != null) {
      const measured = ev.session_total != null ? ` · total: ${ev.session_total}`
                     : ev.threshold_s    != null ? ` · threshold: ${ev.threshold_s}s`
                     : '';
      evidenceHtml = `<span class="tool-evidence">${escapeHtml(ev.metric)} ${escapeHtml(String(ev.threshold))}${escapeHtml(measured)}</span>`;
    }
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">flag_issue</span>
        <span class="tool-arg">${escapeHtml(label)}</span>
        ${evidenceHtml}
        <span class="tool-desc">${escapeHtml(args?.description || '')}</span>
      </span>`;

  } else if (tool === 'get_speech_metrics') {
    const wpm   = args?.wpm_20s   != null ? `${args.wpm_20s} WPM` : '';
    const fills = args?.filler_count_30s != null ? `${args.filler_count_30s} fillers/30s` : '';
    const summary = [wpm, fills].filter(Boolean).join(' · ') || 'metrics';
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">get_speech_metrics</span>
        <span class="tool-arg metrics-result">${escapeHtml(summary)}</span>
      </span>`;

  } else if (tool === 'get_recent_transcript') {
    const n = args?.returned_words ?? args?.n_words ?? '';
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">get_recent_transcript</span>
        <span class="tool-arg">${n ? `last ${n} words` : 'transcript'}</span>
      </span>`;

  } else if (tool === 'check_eye_contact') {
    const confirmed = args?.confirmed;
    const secs = args?.seconds_away != null ? `${args.seconds_away}s away` : '';
    const status = confirmed ? '✓ confirmed' : 'tracking…';
    const cls = confirmed ? 'metrics-result' : '';
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">check_eye_contact</span>
        <span class="tool-arg ${cls}">${escapeHtml(secs ? `${secs} · ${status}` : status)}</span>
      </span>`;

  } else if (tool === 'check_slide_clarity') {
    const signalMap = {
      clutter: 'clutter',
      unreadable_text: 'unreadable text',
      weak_hierarchy: 'weak hierarchy',
      speech_mismatch: 'speech mismatch',
    };
    const signal = signalMap[args?.signal] || args?.signal || 'slide issue';
    const detail = args?.suggested_callout || args?.reason || '';
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">check_slide_clarity</span>
        <span class="tool-arg">${escapeHtml(signal)}</span>
        <span class="tool-desc">${escapeHtml(detail)}</span>
      </span>`;

  } else if (tool === 'navigate_practice_slides') {
    const action = args?.action || 'next';
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">navigate_practice_slides</span>
        <span class="tool-arg">${escapeHtml(action)}</span>
      </span>`;

  } else if (tool === 'jump_to_slide') {
    const idx = args?.index ?? '?';
    const total = args?.total_slides ?? '?';
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">jump_to_slide</span>
        <span class="tool-arg">slide ${Number(idx) + 1} of ${total}</span>
      </span>`;

  } else if (tool === 'mark_slide_issue') {
    const issueLabels = {
      clutter: 'Clutter', font_too_small: 'Font too small',
      low_contrast: 'Low contrast', off_topic: 'Off-topic', missing_data: 'Missing data',
    };
    const issueLabel = issueLabels[args?.issue_type] || args?.issue_type || 'Issue';
    const lbl = args?.label ? ` — ${args.label}` : '';
    const slideNum = args?.slide_index != null ? ` (slide ${Number(args.slide_index) + 1})` : '';
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">mark_slide_issue</span>
        <span class="tool-arg">${escapeHtml(issueLabel + lbl + slideNum)}</span>
      </span>`;

  } else if (tool === 'generate_live_visual_hint') {
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">generate_live_visual_hint</span>
        <span class="tool-arg">${escapeHtml(args?.hint_type || 'visual')}</span>
        <span class="tool-desc">Generating ${escapeHtml(args?.title || 'visual')}…</span>
      </span>`;

  } else {
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">${escapeHtml(tool)}</span>
      </span>`;
  }

  _prependToSuggestions(line);
}

/* ── Architecture telemetry overlay ─────────────── */
const _telemetryHistory = [];   // rolling last-N telemetry rows
const _telemetryFeed    = document.getElementById('telemetry-feed');
const _agentTelemetry   = document.getElementById('agent-telemetry');

function _appendTelemetryEntry(label, keyArg = '') {
  if (!_telemetryFeed) return;
  const now = new Date();
  const ts  = `${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;

  _telemetryHistory.push({ tool: label, keyArg, ts });
  if (_telemetryHistory.length > 8) _telemetryHistory.shift();

  _telemetryFeed.innerHTML = _telemetryHistory.map(e => `
    <div class="telemetry-entry">
      <span class="telemetry-ts">${escapeHtml(e.ts)}</span>
      <span class="telemetry-tool">${escapeHtml(e.tool)}</span>
      ${e.keyArg ? `<span class="telemetry-arg">${escapeHtml(e.keyArg)}</span>` : ''}
    </div>
  `).join('');
}

function updateTelemetry(tool, args) {
  // Build a compact one-line summary for each tool
  let keyArg = '';
  if (tool === 'flag_issue') {
    keyArg = args?.issue_type || '';
  } else if (tool === 'get_speech_metrics') {
    const wpm = args?.wpm_20s != null ? `${args.wpm_20s} WPM` : '';
    const mono = args?.monotony_score != null ? `mono ${args.monotony_score}` : '';
    keyArg = [wpm, mono].filter(Boolean).join(' · ');
  } else if (tool === 'check_eye_contact') {
    keyArg = args?.confirmed ? '✓' : '…';
  } else if (tool === 'check_slide_clarity') {
    keyArg = args?.signal || '';
  } else if (tool === 'generate_live_visual_hint') {
    keyArg = args?.hint_type || 'visual';
  } else if (tool === 'jump_to_slide') {
    keyArg = args?.index != null ? `→${Number(args.index) + 1}` : '';
  } else if (tool === 'mark_slide_issue') {
    keyArg = args?.issue_type || '';
  }
  _appendTelemetryEntry(tool, keyArg);
}

function updateTelemetryEvent(msg) {
  const phase = String(msg?.phase || 'event');
  const detail = String(msg?.detail || '');
  let arg = detail;
  const data = msg?.data || {};
  if (phase === 'coach_reply_start') {
    arg = `${detail}${data?.action ? ` · ${data.action}` : ''}`;
  } else if (phase === 'agent_step') {
    arg = `${detail}${data?.agent ? ` · ${data.agent}` : ''}`;
  } else if (phase === 'status') {
    arg = detail;
  } else if (phase === 'demo_checkpoint') {
    arg = data?.message ? String(data.message) : detail;
  }
  _appendTelemetryEntry(phase, arg);
}

/* ── Slide issue annotation ──────────────────────── */
function showSlideMarkEvent(msg) {
  const issueLabels = {
    clutter: 'Clutter', font_too_small: 'Font too small',
    low_contrast: 'Low contrast', off_topic: 'Off-topic', missing_data: 'Missing data',
  };
  const label = issueLabels[msg.issue_type] || msg.issue_type || 'Slide issue';
  const extra = msg.label ? ` — ${msg.label}` : '';
  const slideNum = msg.slide_index != null ? ` (slide ${Number(msg.slide_index) + 1})` : '';
  const line = document.createElement('div');
  line.className = 'transcript-line tool-call slide-mark-event';
  line.innerHTML = `
    <span class="transcript-speaker tool-speaker">ADK</span>
    <span class="transcript-text tool-text">
      <span class="tool-name">slide annotation</span>
      <span class="tool-arg">${escapeHtml(label + extra + slideNum)}</span>
    </span>`;
  _prependToSuggestions(line);
}

function showDemoSeedEvent(msg) {
  const line = document.createElement('div');
  line.className = 'transcript-line tool-call';
  line.innerHTML = `
    <span class="transcript-speaker tool-speaker">DEMO</span>
    <span class="transcript-text tool-text">
      <span class="tool-name">checkpoint</span>
      <span class="tool-desc">${escapeHtml(msg.message || 'Demo checkpoint')}</span>
    </span>`;
  _prependToSuggestions(line);
}

/* ── Live visual hint (Imagen in-session) ─────────── */
// Map from hint_type → pending DOM element (so we can replace spinner with image)
const _liveVisualPending = {};

function handleLiveVisualHint(msg) {
  if (msg.status === 'generating') {
    const line = document.createElement('div');
    line.className = 'transcript-line tool-call live-visual-line';
    line.id = `lv-pending-${msg.hint_type}`;
    line.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text">
        <span class="tool-name">live visual</span>
        <span class="tool-arg">${escapeHtml(msg.title || msg.hint_type)}</span>
        <span class="tool-desc lv-spinner">Generating image…</span>
      </span>`;
    _liveVisualPending[msg.hint_type] = line;
    _prependToSuggestions(line);

  } else if (msg.status === 'ready' && msg.data_base64) {
    // Replace spinner line with the actual image
    const pending = _liveVisualPending[msg.hint_type];
    const container = pending || document.createElement('div');
    container.className = 'transcript-line live-visual-line';
    container.removeAttribute('id');

    const sourceLabel = msg.source === 'fallback' ? ' (fallback)' : '';
    const afterSrc = `data:${msg.mime_type || 'image/jpeg'};base64,${msg.data_base64}`;
    const hasBefore = !!msg.before_b64;
    const beforeSrc = hasBefore ? `data:image/jpeg;base64,${msg.before_b64}` : '';
    const rationale = (msg.rationale || '').trim();
    const rationaleHtml = rationale ? `<div class="lv-rationale">${escapeHtml(rationale)}</div>` : '';

    const imageContent = hasBefore
      ? `<div class="lv-before-after">
           <div class="lv-ba-col">
             <div class="lv-ba-label">Before</div>
             <img class="lv-image" src="${beforeSrc}" alt="Current slide" />
           </div>
           <div class="lv-ba-col">
             <div class="lv-ba-label">Redesigned</div>
             <img class="lv-image" src="${afterSrc}"
                  alt="${escapeHtml(msg.title || 'Redesigned slide')}"
                  onclick="openLiveVisualModal(this.src, ${JSON.stringify(msg.title || '')})" />
           </div>
         </div>`
      : `<div class="lv-image-wrap">
           <img class="lv-image" src="${afterSrc}"
                alt="${escapeHtml(msg.title || 'Live visual hint')}"
                onclick="openLiveVisualModal(this.src, ${JSON.stringify(msg.title || '')})" />
         </div>`;

    container.innerHTML = `
      <span class="transcript-speaker tool-speaker">ADK</span>
      <span class="transcript-text tool-text lv-result">
        <span class="tool-name">live visual</span>
        <span class="tool-arg">${escapeHtml(msg.title || msg.hint_type)}${escapeHtml(sourceLabel)}</span>
        ${rationaleHtml}
        ${imageContent}
      </span>`;
    if (!pending) _prependToSuggestions(container);
    delete _liveVisualPending[msg.hint_type];
  }
}

function openLiveVisualModal(src, title) {
  const modal = document.getElementById('live-visual-modal');
  if (!modal) return;
  document.getElementById('lv-modal-img').src = src;
  document.getElementById('lv-modal-title').textContent = title || 'Live Visual';
  modal.classList.remove('hidden');
}

function closeLiveVisualModal() {
  const modal = document.getElementById('live-visual-modal');
  if (modal) modal.classList.add('hidden');
}

/* ── Coach toast ─────────────────────────────────── */
let toastTimeout = null;
function showCoachToast(text) {
  toastText.textContent = text;
  coachToast.classList.remove('hidden');
  requestAnimationFrame(() => {
    coachToast.classList.add('visible');
  });

  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    coachToast.classList.remove('visible');
    setTimeout(() => coachToast.classList.add('hidden'), 350);
  }, 5000);
}

/* ── Live metrics ────────────────────────────────── */
function updateMetric(key, value) {
  const metricMap = {
    filler: 'filler',
    eye_contact: 'eye',
    pace: 'pace',
    clarity: 'clarity',
    contradiction: 'clarity',
    slide_clarity: 'visuals',
    slide_mismatch: 'visuals',
  };
  const id = metricMap[key];
  if (!id) return;

  metrics[id] = value;
  const el = document.getElementById(`val-${id}`);
  const chip = document.getElementById(`chip-${id}`);

  if (el) {
    el.textContent = value;
    // Flash animation on change
    el.classList.remove('flash');
    void el.offsetWidth; // force reflow to restart animation
    el.classList.add('flash');
    el.addEventListener('animationend', () => el.classList.remove('flash'), { once: true });
  }
  if (chip) chip.classList.toggle('alert', value >= 3);
}

/* ── Overlay Drawing Logic ──────────────────────── */
function clearOverlay() {
  if (!overlayCtx || !overlayCanvas) return;
  overlayCtx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height);
}

let activeOverlayTimeout = null;
function drawOverlayHighlight(x, y, label) {
  if (!overlayCtx || !overlayCanvas) return;

  // x, y are 0..1 normalized. Map to canvas pixels.
  const px = x * overlayCanvas.width;
  const py = y * overlayCanvas.height;

  clearOverlay();
  if (activeOverlayTimeout) clearTimeout(activeOverlayTimeout);

  // Draw glowing ring
  overlayCtx.beginPath();
  overlayCtx.arc(px, py, 40, 0, 2 * Math.PI);
  overlayCtx.strokeStyle = '#38d9a9';
  overlayCtx.lineWidth = 4;
  overlayCtx.shadowBlur = 15;
  overlayCtx.shadowColor = '#38d9a9';
  overlayCtx.stroke();

  // Draw smaller inner ring
  overlayCtx.beginPath();
  overlayCtx.arc(px, py, 10, 0, 2 * Math.PI);
  overlayCtx.fillStyle = '#38d9a9';
  overlayCtx.fill();

  if (label) {
    overlayCtx.font = 'bold 16px Manrope, sans-serif';
    overlayCtx.fillStyle = '#ffffff';
    overlayCtx.shadowBlur = 4;
    overlayCtx.shadowColor = 'rgba(0,0,0,0.5)';
    overlayCtx.fillText(label, px + 50, py + 5);
  }

  // Clear after 4 seconds
  activeOverlayTimeout = setTimeout(clearOverlay, 4000);
}

/* ── Star rating helpers ─────────────────────────── */

/** Convert a 0-100 score to a 0-5 rating floored to the nearest 0.5. */
function scoreToStars(score) {
  const safe = Number.isFinite(score) ? Math.max(0, Math.min(100, score)) : 0;
  // Floor to the nearest 0.5 so borderline scores (e.g. 95-99) don't render
  // as a perfect 5.0 unless the backend score is truly 100.
  return Math.floor((safe / 20) * 2) / 2;
}

/** Render 5 star spans with full / half / empty CSS classes. */
function renderStarRow(stars) {
  let html = '';
  for (let i = 1; i <= 5; i++) {
    if (i <= Math.floor(stars)) {
      html += '<span class="star full">★</span>';
    } else if (i === Math.ceil(stars) && stars % 1 >= 0.5) {
      html += '<span class="star half">★</span>';
    } else {
      html += '<span class="star empty">★</span>';
    }
  }
  return html;
}

/* ── Scorecard ───────────────────────────────────── */
/* ── Post-session transcript + coach review ─────── */
// Server segments: { s: "u" | "c", t: text, ts: seconds from start }.
// Coach "speech" that is really a leaked tool call (stored by sessions recorded
// before the server-side filter existed).
const TOOL_TEXT_RE = /tool_code|tool_call|default_api|\b(flag_issue|check_eye_contact|get_speech_metrics|draw_overlay|get_recent_transcript)\b/i;

function _transcriptRows(data) {
  return (data.transcript || [])
    .filter((seg) => seg && seg.t && String(seg.t).trim())
    .filter((seg) => !(seg.s === 'c' && TOOL_TEXT_RE.test(String(seg.t))))
    .map((seg) => ({
      speaker: seg.s === 'c' ? 'Coach' : seg.s === 'o' ? 'Them' : 'You',
      text: String(seg.t).trim(),
      ts: Number(seg.ts) || 0,
    }));
}

function _coachMoments(data) {
  return (data.coaching_events || []).map((e) => ({
    type: e.type,
    ts: Number(e.timestamp) || 0,
    description: e.text || '',
    evidence: e.evidence || {},
    source: e.source || 'model',
    said: e.coach_said || '',
    saidKind: 'said',
    userRating: e.user_rating || '',
  }));
}

/** created_at is unix seconds from the server; tolerate milliseconds too. */
function _sessionDate(data) {
  const raw = Number(data.created_at);
  if (!Number.isFinite(raw) || raw <= 0) return new Date();
  return new Date(raw > 1e12 ? raw : raw * 1000);
}

function _userWordCount(rows) {
  return rows.filter((r) => r.speaker === 'You')
    .reduce((n, r) => n + r.text.split(/\s+/).filter(Boolean).length, 0);
}

function _reviewPlainText(data) {
  const rows = _transcriptRows(data);
  const moments = _coachMoments(data);
  const mode = (data.coach_mode || 'general').replace(/_/g, ' ');
  const context = (data.delivery_context || 'virtual').replace(/_/g, ' ');
  const goal = (data.primary_goal || 'balanced').replace(/_/g, ' ');
  const out = [
    'PITCHMIRROR SESSION REVIEW',
    `${_sessionDate(data).toLocaleString()} · ${mode} · ${context} · goal: ${goal}`,
    `Score: ${Number(data.overall_score || 0).toFixed(1)}/100 · Duration: ${formatTime(Number(data.duration_seconds) || 0)} · ${_userWordCount(rows)} words spoken`,
    '',
    '== TRANSCRIPT ==',
  ];
  if (rows.length) rows.forEach((r) => out.push(`[${formatTime(r.ts)}] ${r.speaker}: ${r.text}`));
  else out.push('(no speech was transcribed)');

  out.push('', `== COACHING MOMENTS (${moments.length}) ==`);
  if (!moments.length) out.push('(no coaching interruptions)');
  moments.forEach((m) => {
    out.push(`[${formatTime(m.ts)}] ${ISSUE_LABELS[m.type] || m.type}: ${m.description}`);
    if (m.said) out.push(`    Coach said: "${m.said}"`);
    if (ISSUE_TIPS[m.type]) out.push(`    Try this: ${ISSUE_TIPS[m.type]}`);
    const ev = evidenceSummary(m.evidence);
    if (ev) out.push(`    Evidence: ${ev}`);
  });

  const recap = data.recap || {};
  if (recap.summary || (recap.action_items || []).length) {
    // Recap goes right after the header lines, before the transcript.
    const rc = ['', '== MEETING RECAP =='];
    if (recap.summary) rc.push(recap.summary);
    const sect = (title, items) => { if (items && items.length) { rc.push('', `${title}:`); items.forEach((x) => rc.push(`  - ${x}`)); } };
    sect('Decisions', recap.decisions);
    if ((recap.action_items || []).length) {
      rc.push('', 'Action items:');
      recap.action_items.forEach((a) => rc.push(`  - [${a.owner || '—'}] ${a.task}${a.due ? ` (due ${a.due})` : ''}`));
    }
    sect('Open questions', recap.open_questions);
    sect('Client signals', recap.client_signals);
    if (recap.follow_up_email?.body) {
      rc.push('', '== FOLLOW-UP EMAIL ==', `Subject: ${recap.follow_up_email.subject || ''}`, '', recap.follow_up_email.body);
    }
    out.splice(3, 0, ...rc);
  }
  const points = data.brief?.points || [];
  if (points.length) {
    out.push('', `== PRE-CALL POINTS (${points.filter((p) => p.covered).length}/${points.length} covered) ==`);
    points.forEach((p) => out.push(`  ${p.covered ? '[x]' : '[ ]'} ${p.text}`));
  }

  const qa = Array.isArray(data.qa_review) ? data.qa_review : [];
  if (qa.length) {
    out.push('', `== QUESTION-BY-QUESTION REVIEW (${qa.length}) ==`);
    qa.forEach((q, i) => {
      out.push(`Q${i + 1} [${formatTime(Number(q.ts) || 0)}]${Number.isFinite(q.rating) ? ` (${q.rating}/5)` : ''}: ${q.question}`);
      out.push(`    Your answer: ${q.answer}`);
      if (q.verdict) out.push(`    Verdict: ${q.verdict}`);
      if (q.worked) out.push(`    What worked: ${q.worked}`);
      if (q.improve) out.push(`    Fix this: ${q.improve}`);
      if (q.better_answer) out.push(`    Stronger answer: ${q.better_answer}`);
    });
  }

  const report = String(data.final_report || window._analysisReport || '').replace(/\*\*(.*?)\*\*/g, '$1').trim();
  out.push('', '== AI COACHING REPORT ==', report || '(not available)');
  return out.join('\n') + '\n';
}

function downloadSessionReview() {
  const data = window._lastScorecardData;
  if (!data) return;
  const blob = new Blob([_reviewPlainText(data)], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const stamp = _sessionDate(data).toISOString().slice(0, 16).replace(/[:T]/g, '-');
  a.href = url;
  a.download = `pitchmirror-session-${stamp}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
// Back-compat for any old inline handlers.
const downloadTranscript = downloadSessionReview;

/** After-call recap: summary, decisions, action items, follow-up email, brief coverage. */
function renderRecap(data) {
  const r = data.recap || {};
  const points = data.brief?.points || [];
  const hasRecap = r.summary || (r.action_items || []).length || r.follow_up_email?.body;
  const moods = (data.client_moods || []).filter((m, i, arr) => i === 0 || m.mood !== arr[i - 1].mood);
  if (!hasRecap && !points.length && !moods.length) return '';
  const moodHtml = moods.length
    ? `<div class="rc-block"><div class="rc-title">Client mood during the call</div>
        <div class="rc-moods">${moods.map((m) => `<span class="rc-mood mood-${escapeHtml(m.mood)}" title="${escapeHtml(m.reason || '')}">
          <span class="sc-event-time">${formatTime(Number(m.ts) || 0)}</span> ${escapeHtml(m.mood)}</span>`).join('<span class="rc-arrow">→</span>')}</div></div>`
    : '';
  const list = (items) => items.map((x) => `<li>${escapeHtml(x)}</li>`).join('');
  const block = (title, items) => (items && items.length
    ? `<div class="rc-block"><div class="rc-title">${title}</div><ul>${list(items)}</ul></div>` : '');
  const actions = (r.action_items || []).length
    ? `<div class="rc-block"><div class="rc-title">Action items</div>
        <table class="rc-actions"><thead><tr><th>Owner</th><th>Task</th><th>Due</th></tr></thead><tbody>
        ${r.action_items.map((a) => `<tr><td>${escapeHtml(a.owner || '—')}</td><td>${escapeHtml(a.task)}</td><td>${escapeHtml(a.due || '—')}</td></tr>`).join('')}
        </tbody></table></div>`
    : '';
  const done = points.filter((p) => p.covered).length;
  const coverage = points.length
    ? `<div class="rc-block"><div class="rc-title">Pre-call points covered <span class="report-badge">${done}/${points.length}</span></div>
        <ul class="rc-points">${points.map((p) => `<li class="${p.covered ? 'done' : 'missed'}">${p.covered ? '✓' : '✗'} ${escapeHtml(p.text)}${p.covered && Number.isFinite(p.ts) ? ` <span class="sc-event-time">${formatTime(p.ts)}</span>` : ''}</li>`).join('')}</ul></div>`
    : '';
  const email = r.follow_up_email?.body
    ? `<div class="rc-email">
        <div class="rc-title">Follow-up email <span class="rc-email-actions">
          <button type="button" class="asset-download" onclick="copyRecapEmail()">Copy</button>
          <button type="button" class="asset-download" onclick="openRecapEmail()">Open in mail app</button>
        </span></div>
        <div class="rc-email-subject"><b>Subject:</b> ${escapeHtml(r.follow_up_email.subject || '')}</div>
        <pre class="rc-email-body">${escapeHtml(r.follow_up_email.body)}</pre>
        <span id="rc-email-status" class="rc-email-status" aria-live="polite"></span>
      </div>`
    : '';
  return `
    <div class="recap-section">
      <div class="sc-events-title">Meeting recap</div>
      ${r.summary ? `<p class="rc-summary">${escapeHtml(r.summary)}</p>` : ''}
      <div class="rc-grid">
        ${coverage}
        ${moodHtml}
        ${block('Decisions', r.decisions)}
        ${actions}
        ${block('Open questions', r.open_questions)}
        ${block('Client signals', r.client_signals)}
      </div>
      ${email}
    </div>`;
}

function _recapEmailText() {
  const e = window._lastScorecardData?.recap?.follow_up_email || {};
  return { subject: e.subject || '', body: e.body || '' };
}

async function copyRecapEmail() {
  const { subject, body } = _recapEmailText();
  const status = document.getElementById('rc-email-status');
  try {
    await navigator.clipboard.writeText(`Subject: ${subject}\n\n${body}`);
    if (status) status.textContent = 'Copied ✓';
  } catch (_) {
    if (status) status.textContent = 'Copy failed — select the text and copy it manually.';
  }
}

function openRecapEmail() {
  const { subject, body } = _recapEmailText();
  window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

/** Pilot feedback form shown under every review. */
function renderFeedbackForm(data) {
  if (!data.session_id) return '';
  const tester = window.PMTester?.current()?.name || '';
  const stars = [1, 2, 3, 4, 5].map((n) => `
    <button type="button" class="fb-star" data-value="${n}" aria-label="${n} star${n > 1 ? 's' : ''}">★</button>`).join('');
  return `
    <form class="feedback-section" id="feedback-form" onsubmit="submitFeedback(event)">
      <div class="sc-events-title">How useful was this? <span class="report-badge">pilot feedback${tester ? ` · ${escapeHtml(tester)}` : ''}</span></div>
      <div class="fb-row">
        <span class="fb-label">Rating</span>
        <span class="fb-stars" role="radiogroup" aria-label="Rating">${stars}</span>
      </div>
      <label class="fb-row"><span class="fb-label">What helped most?</span>
        <textarea name="helped" rows="2" maxlength="1000" placeholder="e.g. the answer outlines, the follow-up email"></textarea></label>
      <label class="fb-row"><span class="fb-label">What was annoying or wrong?</span>
        <textarea name="annoying" rows="2" maxlength="1000" placeholder="e.g. too many nudges, missed my words"></textarea></label>
      <div class="fb-row"><span class="fb-label">Would you use it in real meetings?</span>
        <span class="fb-choices">
          <label><input type="radio" name="would_use" value="yes"> Yes</label>
          <label><input type="radio" name="would_use" value="maybe"> Maybe</label>
          <label><input type="radio" name="would_use" value="no"> No</label>
        </span>
      </div>
      <div class="fb-actions">
        <button type="submit" class="asset-download fb-submit">Send feedback</button>
        <span id="fb-status" class="fb-status" aria-live="polite"></span>
      </div>
    </form>`;
}

function bindFeedbackForm() {
  const form = document.getElementById('feedback-form');
  if (!form) return;
  form.querySelectorAll('.fb-star').forEach((btn) => {
    btn.addEventListener('click', () => {
      form.dataset.rating = btn.dataset.value;
      form.querySelectorAll('.fb-star').forEach((b) => b.classList.toggle('on', Number(b.dataset.value) <= Number(btn.dataset.value)));
    });
  });
}

async function submitFeedback(event) {
  event.preventDefault();
  const form = event.target;
  const status = document.getElementById('fb-status');
  const rating = Number(form.dataset.rating || 0);
  if (!rating) { status.textContent = 'Pick a star rating first.'; return; }
  const payload = {
    session_id: window._lastScorecardData?.session_id,
    rating,
    helped: form.helped.value,
    annoying: form.annoying.value,
    would_use: form.querySelector('input[name="would_use"]:checked')?.value || '',
    tester: window.PMTester?.current()?.name || '',
  };
  status.textContent = 'Sending…';
  try {
    const resp = await fetch('/api/feedback', {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    status.textContent = 'Thanks — feedback saved ✓';
    form.querySelector('.fb-submit').textContent = 'Update feedback';
  } catch (err) {
    console.error('feedback failed', err);
    status.textContent = 'Could not save feedback — try again.';
  }
}

/** Question-by-question review of a meeting: each question asked, the answer, a rating and a stronger answer. */
function renderQaReview(data) {
  const items = Array.isArray(data.qa_review) ? data.qa_review : [];
  if (!items.length) {
    if (data.session_kind !== 'meeting') return '';
    return `
      <div class="qa-section">
        <div class="sc-events-title">Question-by-question review</div>
        <div class="recent-empty">No questions from the other person were captured. Use “Choose meeting audio”
        before starting so the coach can hear their questions.</div>
      </div>`;
  }
  const cards = items.map((q, i) => {
    const stars = Number.isFinite(q.rating) ? renderStarRow(q.rating) : '';
    const hint = (q.hint_points || []).length
      ? `<div class="qa-row"><span class="qa-label">Live outline shown</span><ol class="qa-hint">${q.hint_points.map((p) => `<li>${escapeHtml(p)}</li>`).join('')}</ol></div>`
      : '';
    return `
      <div class="qa-card">
        <div class="qa-head">
          <span class="qa-num">Q${i + 1}</span>
          <span class="sc-event-time">${formatTime(Number(q.ts) || 0)}</span>
          ${stars ? `<span class="qa-stars">${stars}<span class="qa-rating">${q.rating}/5</span></span>` : ''}
        </div>
        <div class="qa-question">“${escapeHtml(q.question)}”</div>
        <div class="qa-row"><span class="qa-label">Your answer</span><span class="qa-answer">${escapeHtml(q.answer)}</span></div>
        ${q.verdict ? `<div class="qa-row"><span class="qa-label">Verdict</span><span>${escapeHtml(q.verdict)}</span></div>` : ''}
        ${q.worked ? `<div class="qa-row"><span class="qa-label">What worked</span><span>${escapeHtml(q.worked)}</span></div>` : ''}
        ${q.improve ? `<div class="qa-row"><span class="qa-label">Fix this</span><span>${escapeHtml(q.improve)}</span></div>` : ''}
        ${hint}
        ${q.better_answer ? `<div class="qa-better"><span class="qa-label">Stronger answer</span><p>${escapeHtml(q.better_answer)}</p></div>` : ''}
      </div>`;
  }).join('');
  return `
    <div class="qa-section">
      <div class="sc-events-title">Question-by-question review <span class="report-badge">${items.length} question${items.length === 1 ? '' : 's'}</span></div>
      <div class="qa-list">${cards}</div>
    </div>`;
}

/** Scroll the review transcript to coaching moment `i` and flash it. */
function jumpToMoment(i) {
  const marker = document.getElementById(`rv-marker-${i}`);
  const col = document.getElementById('rv-transcript-body');
  if (!marker || !col) return;
  col.scrollTo({ top: marker.offsetTop - col.offsetTop - 24, behavior: 'smooth' });
  marker.classList.remove('flash');
  void marker.offsetWidth;
  marker.classList.add('flash');
}

/** Transcript (left) and coach review (right), side by side. */
function renderReviewSection(data, reportBodyHtml, modeLabel) {
  const rows = _transcriptRows(data);
  const moments = _coachMoments(data);
  const words = _userWordCount(rows);

  // Interleave coaching-moment markers into the transcript by timestamp.
  const items = rows.map((r) => ({ kind: 'line', ts: r.ts, r }))
    .concat(moments.map((m, i) => ({ kind: 'moment', ts: m.ts, m, i })))
    .sort((x, y) => (x.ts - y.ts) || (x.kind === 'line' ? -1 : 1));
  const transcriptHtml = rows.length || moments.length
    ? items.map((it) => (it.kind === 'line'
      ? `<div class="sc-transcript-line ${it.r.speaker === 'Coach' ? 'is-coach' : it.r.speaker === 'Them' ? 'is-them' : 'is-user'}">
           <span class="sc-event-time">${formatTime(it.r.ts)}</span>
           <span class="sc-transcript-speaker">${it.r.speaker}</span>
           <span class="sc-transcript-text">${escapeHtml(it.r.text)}</span>
         </div>`
      : `<div id="rv-marker-${it.i}" class="rv-marker type-${escapeHtml(it.m.type)}">
           <span class="sc-event-time">${formatTime(it.m.ts)}</span>
           <span>⚑ ${escapeHtml(ISSUE_LABELS[it.m.type] || it.m.type)}${it.m.said ? ` — coach: “${escapeHtml(it.m.said)}”` : ''}</span>
         </div>`)).join('')
    : '<div class="recent-empty">No speech was transcribed in this session.</div>';

  const momentsHtml = moments.length
    ? moments.map((m, i) => `
        <div role="button" tabindex="0" class="suggestion-card review-card type-${escapeHtml(m.type)}"
             onclick="jumpToMoment(${i})" onkeydown="if (event.key === 'Enter') jumpToMoment(${i})"
             title="Show this moment in the transcript">
          ${suggestionCardHtml(m)}
        </div>`).join('')
    : '<div class="recent-empty">No coaching interruptions this session.</div>';

  return `
    <div class="review-split">
      <div class="review-col">
        <div class="sc-events-title">
          Transcript <span class="report-badge">${words} words</span>
          <button type="button" class="asset-download review-download" onclick="downloadSessionReview()">Download .txt</button>
        </div>
        <div id="rv-transcript-body" class="sc-transcript-body review-body">${transcriptHtml}</div>
      </div>
      <div class="review-col">
        <div class="sc-events-title">Coach Review <span class="report-badge">${moments.length} moments</span></div>
        <div class="review-body review-coach">
          <div class="rv-subhead">Coaching moments</div>
          <div class="review-moments">${momentsHtml}</div>
          <div class="rv-subhead">AI coaching report <span class="report-badge">${escapeHtml(modeLabel)} mode</span></div>
          <div class="report-body">${reportBodyHtml}</div>
        </div>
      </div>
    </div>`;
}

function renderScorecard(data) {
  window._lastScorecardData = data;
  const score = data.overall_score || 0;
  const gradeClass = score >= 80 ? 'excellent' : score >= 65 ? 'good' : score >= 45 ? 'needs-work' : 'poor';
  const gradeLabel = score >= 80 ? 'Excellent' : score >= 65 ? 'Good' : score >= 45 ? 'Needs Work' : 'Poor';
  const modeLabel = (data.coach_mode || 'general').replace('_', ' ');
  const contextLabel = data.session_kind === 'meeting'
    ? `live meeting${data.platform ? ` on ${PLATFORM_LABELS[data.platform] || data.platform}` : ''}`
    : (data.delivery_context || 'virtual').replace('_', ' ');
  const goalLabel = (data.primary_goal || 'balanced').replace('_', ' ');

  const overallStars = scoreToStars(score);
  const insufficientHtml = data.insufficient_speech
    ? `<div class="insufficient-banner">Not enough of your speech was captured to score this session
        (${Number(data.user_words || 0)} words). Check the microphone and language, and speak for at least 30 seconds.</div>`
    : '';
  const scoreMeta = data.scoring || null;
  const scoreMetaHtml = (scoreMeta && Number.isFinite(scoreMeta.base_score))
    ? `<div class="scorecard-grade-breakdown">Base ${scoreMeta.base_score}/100 · Penalty -${Number(scoreMeta.event_penalty || 0).toFixed(1)}</div>`
    : '';

  const categories = data.categories || {};
  const timelineHtml = renderReplayTimeline(data);

  // SVG score ring (arc reflects internal 0-100 percentage)
  const r = 52, cx = 60, cy = 60;
  const circ = +(2 * Math.PI * r).toFixed(2);
  const dashTarget = +(circ * (1 - score / 100)).toFixed(2);
  const ringColorMap = {
    excellent:    '#38d9a9',
    good:         '#4dd4c4',
    'needs-work': '#ffd04d',
    poor:         '#ff4757',
  };
  const ringColor = ringColorMap[gradeClass] || '#f0a035';

  const catHtml = Object.entries(categories).map(([key, cat]) => {
    const catName = {
      filler_words: 'Filler Words',
      eye_contact: 'Eye Contact',
      pace: 'Pace',
      clarity: 'Clarity & Logic',
      visual_delivery: 'Slide Clarity',
      prosody: 'Prosody',
    }[key] || key;
    const catStars = scoreToStars(cat.score);
    const detail = cat.count !== undefined ? `${cat.count} detected` :
                   cat.drops !== undefined ? `${cat.drops} drops` :
                   cat.violations !== undefined ? `${cat.violations} flags` :
                   cat.monotony_score !== undefined
                     ? `monotony ${Number(cat.monotony_score).toFixed(1)} · pause ${(Number(cat.pause_ratio_20s || 0) * 100).toFixed(0)}%`
                     :
                   cat.slide_clarity_flags !== undefined ? `${cat.slide_clarity_flags + (cat.slide_mismatch_flags || 0)} visual issues` :
                   `${(cat.contradictions || 0) + (cat.flags || 0)} issues`;
    return `
      <div class="scorecard-category">
        <div class="sc-cat-name">${escapeHtml(catName)}</div>
        <div class="sc-cat-stars">
          <span class="sc-cat-star-row">${renderStarRow(catStars)}</span>
          <span class="sc-cat-rating">${catStars.toFixed(1)}/5</span>
        </div>
        <div class="sc-cat-detail">${escapeHtml(cat.label)} &middot; ${detail}</div>
      </div>
    `;
  }).join('');

  const durationMin = Math.floor((data.duration_seconds || 0) / 60);
  const durationSec = Math.round((data.duration_seconds || 0) % 60);

  // Final report from post-session multi-agent pipeline
  const report = data.final_report || window._analysisReport || '';
  const generatedAssets = data.generated_assets || window._generatedAssets || [];
  const citationHtml = renderCitationCards(data.research_tips || window._researchTips || '');
  const assetsHtml = renderGeneratedAssets(generatedAssets);
  // Always render the "AI Coaching Report" section; fall back to a placeholder
  // when the report is empty (e.g. very short session, analysis timeout, WS error).
  const reportBody = report
    ? escapeHtml(report).replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    : '<span style="color:var(--text-muted);font-style:italic">AI analysis is pending or unavailable for this session.</span>';
  const reviewHtml = renderReviewSection(data, reportBody, modeLabel);

  scorecardContent.innerHTML = `
    <div class="scorecard-overall">
      <div class="score-ring-wrap">
        <svg class="score-svg" viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle class="ring-track" cx="${cx}" cy="${cy}" r="${r}" />
          <circle class="ring-fill" cx="${cx}" cy="${cy}" r="${r}"
            stroke="${ringColor}"
            stroke-dasharray="${circ}"
            stroke-dashoffset="${circ}"
            data-target="${dashTarget}"
            transform="rotate(-90 ${cx} ${cy})" />
        </svg>
        <div class="score-number ${gradeClass}">${overallStars.toFixed(1)}</div>
      </div>
      <div class="overall-stars">${renderStarRow(overallStars)}</div>
      <div class="scorecard-grade-label">${gradeLabel} &middot; ${overallStars.toFixed(1)}&thinsp;/&thinsp;5 &middot; ${score.toFixed(1)}/100 &middot; ${durationMin}:${String(durationSec).padStart(2,'0')} session &middot; ${escapeHtml(modeLabel)} &middot; ${escapeHtml(contextLabel)} &middot; goal: ${escapeHtml(goalLabel)}</div>
      ${scoreMetaHtml}
    </div>
    ${insufficientHtml}
    ${renderRecap(data)}
    <div class="scorecard-categories">${catHtml}</div>
    ${reviewHtml}
    ${renderQaReview(data)}
    ${timelineHtml}
    ${assetsHtml}
    ${citationHtml}
    ${renderFeedbackForm(data)}
    <div class="recent-section">
      <div class="sc-events-title">Recent Sessions</div>
      <div id="recent-sessions" class="recent-sessions">
        <div class="recent-empty">Loading recent sessions...</div>
      </div>
    </div>
  `;

  scorecardPanel.classList.remove('hidden');
  liveMetrics.classList.add('hidden');
  if (_agentTelemetry) _agentTelemetry.classList.add('hidden');
  videoOverlay.classList.add('hidden');

  // Enable scrolling on right panel for tall scorecard and scroll to top
  const rightPanel = document.querySelector('.right-panel');
  if (rightPanel) {
    rightPanel.classList.add('results-mode');
    rightPanel.scrollTop = 0;
  }

  // Animate score ring arc
  requestAnimationFrame(() => {
    const arc = scorecardContent.querySelector('.ring-fill');
    if (arc) {
      const t = arc.dataset.target;
      requestAnimationFrame(() => { arc.style.strokeDashoffset = t; });
    }
  });

  bindReplayTimeline();
  bindFeedbackForm();
}

function renderReplayTimeline(data) {
  const duration = Math.max(1, Math.ceil(Number(data.duration_seconds || 0)));
  const rawTimeline = Array.isArray(data.timeline_events) ? data.timeline_events : [];
  const markers = rawTimeline
    .map((e, idx) => {
      const ts = Number(e.ts);
      if (!Number.isFinite(ts)) return null;
      const type = String(e.event_type || 'event');
      return {
        idx,
        ts: Math.max(0, Math.min(duration, ts)),
        type,
        label: String(e.label || type),
      };
    })
    .filter(Boolean)
    .sort((a, b) => a.ts - b.ts)
    .slice(0, 240)
    .map((m, idx) => ({ ...m, idx }));

  _replayTimeline = { markers, duration };
  if (!markers.length) {
    return '';
  }

  const markerHtml = markers.map((m, i) => {
    const left = ((m.ts / duration) * 100).toFixed(2);
    return `<span class="timeline-marker type-${escapeHtml(m.type)}" data-marker-index="${i}" style="left:${left}%"></span>`;
  }).join('');

  return `
    <div class="replay-section">
      <div class="sc-events-title">Session Replay Timeline</div>
      <div class="replay-controls">
        <input id="timeline-scrubber" class="timeline-scrubber" type="range" min="0" max="${duration}" step="1" value="0" />
        <span id="timeline-time" class="timeline-time">0:00</span>
      </div>
      <div id="timeline-track" class="timeline-track">${markerHtml}</div>
      <div id="timeline-detail" class="timeline-detail">Move the scrubber to inspect interruptions, signal spikes, and slide changes.</div>
    </div>
  `;
}

function bindReplayTimeline() {
  const scrubber = document.getElementById('timeline-scrubber');
  if (!scrubber || !_replayTimeline.markers.length) return;
  const onUpdate = () => updateReplayTimeline(Number(scrubber.value || 0));
  scrubber.addEventListener('input', onUpdate);
  scrubber.addEventListener('change', onUpdate);
  updateReplayTimeline(0);
}

function updateReplayTimeline(cursorS) {
  const markers = _replayTimeline.markers || [];
  const duration = _replayTimeline.duration || 1;
  const timeEl = document.getElementById('timeline-time');
  const detailEl = document.getElementById('timeline-detail');
  if (timeEl) timeEl.textContent = formatTime(Math.max(0, Math.min(duration, cursorS)));
  if (!detailEl || !markers.length) return;

  let nearest = null;
  for (const m of markers) {
    if (m.ts <= cursorS + 0.5) nearest = m;
    else break;
  }
  if (!nearest) nearest = markers[0];

  const markerNodes = document.querySelectorAll('.timeline-marker');
  markerNodes.forEach((el, idx) => {
    el.classList.toggle('active', idx === nearest.idx);
  });

  const niceType = nearest.type.replace(/_/g, ' ');
  detailEl.textContent = `${formatTime(nearest.ts)} · ${niceType} · ${nearest.label}`;
}

async function loadRecentSessions() {
  const container = document.getElementById('recent-sessions');
  if (!container) return;

  try {
    const resp = await fetch('/api/sessions?limit=5', {
      headers: authHeaders(),
    });
    if (!resp.ok) {
      container.innerHTML = '<div class="recent-empty">Recent session history unavailable.</div>';
      return;
    }

    const payload = await resp.json();
    const sessions = (payload.sessions || []).filter(Boolean);
    if (!sessions.length) {
      container.innerHTML = '<div class="recent-empty">No previous sessions found.</div>';
      return;
    }

    container.innerHTML = sessions.map((s) => {
      const score = Number.isFinite(s.overall_score) ? s.overall_score : 0;
      const when = s.created_at ? new Date(s.created_at * 1000).toLocaleString() : 'unknown';
      const dur = Number.isFinite(s.duration_seconds) ? `${Math.round(s.duration_seconds)}s` : '0s';
      const mode = (s.coach_mode || 'general').replace('_', ' ');
      const context = (s.delivery_context || 'virtual').replace('_', ' ');
      const sid = String(s.session_id || '');
      return `
        <div class="recent-row is-clickable" role="button" tabindex="0"
             onclick="openPastSession('${escapeHtml(sid)}')"
             onkeydown="if (event.key === 'Enter') openPastSession('${escapeHtml(sid)}')"
             title="Open this session's transcript and review">
          <span class="recent-score">${score}</span>
          <span class="recent-meta">${escapeHtml(when)} · ${escapeHtml(dur)} · ${escapeHtml(mode)} · ${escapeHtml(context)}</span>
          <span class="recent-id">${escapeHtml(String(s.session_id || '').slice(0, 8))}</span>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('recent sessions fetch failed', err);
    container.innerHTML = '<div class="recent-empty">Recent session history unavailable.</div>';
  }
}

/* ── Helpers ─────────────────────────────────────── */
/* ── Past sessions (history) ─────────────────────── */
const PLATFORM_LABELS = {
  zoom: 'Zoom', google_meet: 'Google Meet', teams: 'Microsoft Teams', webex: 'Webex', other: 'Other platform',
};
let _historyReturnTo = 'live';

function _setRightView(view) {
  // view: 'live' | 'history' | 'scorecard'
  const rightPanel = document.querySelector('.right-panel');
  liveBoard?.classList.toggle('hidden', view !== 'live');
  historyPanel?.classList.toggle('hidden', view !== 'history');
  scorecardPanel.classList.toggle('hidden', view !== 'scorecard');
  document.getElementById('analysis-panel')?.classList.add('hidden');
  rightPanel?.classList.toggle('results-mode', view !== 'live');
  if (rightPanel) rightPanel.scrollTop = 0;
}

async function showHistory() {
  if (sessionActive || _waitingForScorecard) return;
  _historyReturnTo = scorecardPanel.classList.contains('hidden') ? 'live' : 'scorecard';
  _setRightView('history');
  if (!historyList) return;
  historyList.innerHTML = '<div class="recent-empty">Loading past sessions…</div>';
  try {
    const resp = await fetch('/api/sessions?limit=50', { headers: authHeaders() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const sessions = ((await resp.json()).sessions || []).filter(Boolean);
    if (!sessions.length) {
      historyList.innerHTML = '<div class="recent-empty">No saved sessions yet. Finish a session and it will appear here.</div>';
      return;
    }
    historyList.innerHTML = sessions.map((s) => {
      const sid = escapeHtml(String(s.session_id || ''));
      const score = Number.isFinite(s.overall_score) && !s.insufficient_speech ? Number(s.overall_score).toFixed(1) : '–';
      const when = _sessionDate(s).toLocaleString();
      const mode = escapeHtml((s.coach_mode || 'general').replace(/_/g, ' '));
      const context = s.session_kind === 'meeting'
        ? `live meeting${s.platform ? ` · ${escapeHtml(PLATFORM_LABELS[s.platform] || s.platform)}` : ''}`
        : escapeHtml((s.delivery_context || 'virtual').replace(/_/g, ' '));
      const kindBadge = s.session_kind === 'meeting'
        ? '<span class="history-kind is-meeting">Meeting</span>'
        : '<span class="history-kind">Practice</span>';
      const extra = [
        formatTime(Number(s.duration_seconds) || 0),
        s.words != null ? `${s.words} words` : '',
        s.coaching_moments != null ? `${s.coaching_moments} coaching moments` : '',
      ].filter(Boolean).join(' · ');
      return `
        <div class="history-row" role="button" tabindex="0"
             onclick="openPastSession('${sid}')" onkeydown="if (event.key === 'Enter') openPastSession('${sid}')">
          <span class="history-score">${score}</span>
          <span class="history-meta">
            <span class="history-when">${escapeHtml(when)} ${kindBadge}</span>
            <span class="history-sub">${mode} · ${context} · ${escapeHtml(extra)}</span>
          </span>
          <span class="history-open">View transcript &amp; review →</span>
        </div>`;
    }).join('');
  } catch (err) {
    console.error('history fetch failed', err);
    historyList.innerHTML = '<div class="recent-empty">Could not load past sessions.</div>';
  }
}

function closeHistory() {
  _setRightView(_historyReturnTo === 'scorecard' && window._lastScorecardData ? 'scorecard' : 'live');
}

async function openPastSession(sessionId) {
  if (!sessionId || sessionActive) return;
  try {
    const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, { headers: authHeaders() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    // Saved sessions carry their own report; don't fall back to the last live one.
    window._analysisReport = null;
    window._researchTips = null;
    window._generatedAssets = null;
    _setRightView('scorecard');
    renderScorecard(data);
    loadRecentSessions();
  } catch (err) {
    console.error('open session failed', err);
    if (historyList && !historyPanel.classList.contains('hidden')) {
      historyList.insertAdjacentHTML('afterbegin', '<div class="recent-empty">Could not open that session.</div>');
    }
  }
}

document.getElementById('btn-history')?.addEventListener('click', showHistory);

// Deep links from Meeting Mode: /app?session=<id> opens a saved review, /app?view=history the list.
(() => {
  const params = new URLSearchParams(location.search);
  const sid = params.get('session');
  if (sid) openPastSession(sid);
  else if (params.get('view') === 'history') showHistory();
})();
document.getElementById('btn-history-welcome')?.addEventListener('click', showHistory);
document.getElementById('btn-history-close')?.addEventListener('click', closeHistory);

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function markPipelineStepDone(id) {
  const step = document.getElementById(id);
  if (!step) return;
  step.classList.add('done');
  const dot = step.querySelector('.step-dot');
  if (dot) { dot.classList.remove('spinning'); dot.classList.add('done'); }
}

function renderGeneratedAssets(assets) {
  if (!Array.isArray(assets) || assets.length === 0) return '';

  const cards = assets.slice(0, 2).map((asset) => {
    const mime = asset?.mime_type || 'image/jpeg';
    const data = asset?.data_base64 || '';
    if (!data) return '';
    const dataUrl = `data:${mime};base64,${data}`;
    const filename = `${asset?.id || 'visual'}.jpg`;

    return `
      <div class="asset-card">
        <img class="asset-preview" src="${dataUrl}" alt="${escapeHtml(asset?.title || 'Generated visual')}" />
        <div class="asset-meta">
          <div class="asset-title">${escapeHtml(asset?.title || 'Generated visual')}</div>
          <div class="asset-desc">${escapeHtml(asset?.description || '')}</div>
          <div class="asset-actions">
            <span class="asset-source">${escapeHtml((asset?.source || 'generated').toUpperCase())}</span>
            <a class="asset-download" href="${dataUrl}" download="${filename}">Download</a>
          </div>
        </div>
      </div>
    `;
  }).join('');

  if (!cards.trim()) return '';
  return `
    <div class="asset-section">
      <div class="sc-events-title">Multimodal Visuals <span class="report-badge">2-image cap</span></div>
      <div class="asset-grid">${cards}</div>
    </div>
  `;
}

function renderCitationCards(researchTips) {
  if (!researchTips || !researchTips.trim()) return '';
  // Extract bullet lines from the research agent output
  const bullets = researchTips.split('\n')
    .map(l => l.replace(/^[-•*]\s*/, '').trim())
    .filter(l => l.length > 20);  // skip empty / header lines
  if (!bullets.length) return '';

  const cards = bullets.map(b => {
    // Extract **bold name** if present
    const nameMatch = b.match(/\*\*(.*?)\*\*/);
    const name = nameMatch ? nameMatch[1] : null;
    const rest = nameMatch ? b.replace(/\*\*.*?\*\*:?\s*/, '') : b;
    // Extract trailing (citation) in parens
    const citeMatch = rest.match(/\(([^)]{5,})\)\s*\.?\s*$/);
    const citation = citeMatch ? citeMatch[1] : null;
    const desc = (citeMatch ? rest.slice(0, -citeMatch[0].length) : rest).trim().replace(/\.$/, '');
    return `
      <div class="citation-card">
        ${name ? `<div class="citation-name">${escapeHtml(name)}</div>` : ''}
        <div class="citation-desc">${escapeHtml(desc)}.</div>
        ${citation ? `<div class="citation-source">— ${escapeHtml(citation)}</div>` : ''}
      </div>`;
  }).join('');

  return `
    <div class="citation-section">
      <div class="sc-events-title">Evidence-Based Techniques <span class="report-badge">google search · cited</span></div>
      <div class="citation-cards">${cards}</div>
    </div>`;
}
