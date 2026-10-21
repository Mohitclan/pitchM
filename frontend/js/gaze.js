/**
 * Pitchlight — in-browser gaze tracking (MediaPipe Face Landmarker).
 *
 * Runs face landmark detection on the local webcam ~6 times per second and
 * classifies each moment as one of:
 *   "camera"  — facing the camera
 *   "away"    — head or eyes turned sideways
 *   "notes"   — looking down (reading notes)
 *   "no_face" — no face in frame
 *
 * Only the label is reported (onState callback); no video leaves the page
 * through this module. The server times the streaks and decides what counts
 * as a look-away for the current delivery context.
 */
(function () {
  const TASKS_VISION_URL = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs';
  const WASM_ROOT = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm';
  const MODEL_URL = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task';

  const DETECT_INTERVAL_MS = 160;   // ~6 fps is plenty for multi-second streaks
  const HEARTBEAT_MS = 1000;        // re-send current state so the server can time streaks
  const MIN_SEND_GAP_MS = 500;      // never send more than 2 gaze messages per second
  const SMOOTH_WINDOW = 5;          // majority vote over the last N raw labels
  const CALIBRATION_FRAMES = 12;    // frames used to learn the presenter's neutral head pitch

  // Landmark indices (MediaPipe 468-point face mesh)
  const NOSE_TIP = 1, FOREHEAD = 10, CHIN = 152, CHEEK_L = 234, CHEEK_R = 454;

  let landmarkerPromise = null;
  let timer = null;
  let videoEl = null;
  let onState = null;
  let recent = [];
  let current = null;
  let lastSentAt = 0;
  let lastVideoTime = -1;
  let pitchSamples = [];
  let pitchBaseline = null;

  function loadLandmarker() {
    if (!landmarkerPromise) {
      landmarkerPromise = import(TASKS_VISION_URL).then(async (vision) => {
        const fileset = await vision.FilesetResolver.forVisionTasks(WASM_ROOT);
        return vision.FaceLandmarker.createFromOptions(fileset, {
          baseOptions: { modelAssetPath: MODEL_URL, delegate: 'GPU' },
          runningMode: 'VIDEO',
          numFaces: 1,
          outputFaceBlendshapes: true,
        });
      }).then((landmarker) => {
        // Run one detection up front: the first call compiles GPU/WASM kernels and
        // can freeze the page for seconds. Doing it before the session starts keeps
        // audio chunks from piling up and bursting past the server's rate limit.
        try {
          const c = document.createElement('canvas');
          c.width = 64; c.height = 48;
          c.getContext('2d').fillRect(0, 0, 64, 48);
          landmarker.detectForVideo(c, performance.now());
        } catch (e) { /* warm-up is best effort */ }
        return landmarker;
      }).catch((err) => {
        landmarkerPromise = null;  // allow a retry on the next session
        throw err;
      });
    }
    return landmarkerPromise;
  }

  function blend(categories, name) {
    const c = categories.find((x) => x.categoryName === name);
    return c ? c.score : 0;
  }

  function median(arr) {
    const s = [...arr].sort((a, b) => a - b);
    return s[Math.floor(s.length / 2)];
  }

  function classify(result) {
    const faces = result.faceLandmarks || [];
    if (!faces.length) return 'no_face';
    const lm = faces[0];

    // Head yaw: where the nose sits between the two cheeks (≈0.5 when facing camera).
    const width = lm[CHEEK_R].x - lm[CHEEK_L].x;
    const yawRatio = width !== 0 ? (lm[NOSE_TIP].x - lm[CHEEK_L].x) / width : 0.5;

    // Head pitch: where the nose sits between forehead and chin; grows when looking down.
    const height = lm[CHIN].y - lm[FOREHEAD].y;
    const pitchRatio = height !== 0 ? (lm[NOSE_TIP].y - lm[FOREHEAD].y) / height : 0.5;

    const shapes = (result.faceBlendshapes && result.faceBlendshapes[0]?.categories) || [];
    const eyesDown = (blend(shapes, 'eyeLookDownLeft') + blend(shapes, 'eyeLookDownRight')) / 2;
    const eyesLeft = (blend(shapes, 'eyeLookOutLeft') + blend(shapes, 'eyeLookInRight')) / 2;
    const eyesRight = (blend(shapes, 'eyeLookInLeft') + blend(shapes, 'eyeLookOutRight')) / 2;

    const headTurned = yawRatio < 0.32 || yawRatio > 0.68;
    const eyesSideways = Math.max(eyesLeft, eyesRight) > 0.65;

    // Learn the neutral pitch from the first frames where the presenter faces forward.
    if (pitchBaseline === null && !headTurned && eyesDown < 0.4) {
      pitchSamples.push(pitchRatio);
      if (pitchSamples.length >= CALIBRATION_FRAMES) pitchBaseline = median(pitchSamples);
    }
    const headDown = pitchBaseline !== null && pitchRatio > pitchBaseline + 0.07;

    if (eyesDown > 0.55 || headDown) return 'notes';
    if (headTurned || eyesSideways) return 'away';
    return 'camera';
  }

  function smoothed(label) {
    recent.push(label);
    if (recent.length > SMOOTH_WINDOW) recent.shift();
    const counts = {};
    for (const l of recent) counts[l] = (counts[l] || 0) + 1;
    return Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0];
  }

  function emit(state, force) {
    const now = performance.now();
    if (now - lastSentAt < MIN_SEND_GAP_MS) return;
    if (!force && state === current && now - lastSentAt < HEARTBEAT_MS) return;
    current = state;
    lastSentAt = now;
    try { onState && onState(state); } catch (e) { console.warn('gaze onState failed', e); }
  }

  let landmarkerRef = null;
  let lastDetectAt = 0;

  /** Run one detection if enough time has passed since the last one. */
  function tick() {
    if (!landmarkerRef || !videoEl || videoEl.readyState < 2) return;
    const now = performance.now();
    if (now - lastDetectAt < DETECT_INTERVAL_MS) return;
    lastDetectAt = now;
    if (videoEl.currentTime === lastVideoTime) return;
    lastVideoTime = videoEl.currentTime;
    let result;
    try {
      result = landmarkerRef.detectForVideo(videoEl, now);
    } catch (err) {
      return;  // transient decode/GPU hiccup — skip this frame
    }
    const state = smoothed(classify(result));
    emit(state, state !== current);
  }

  /**
   * opts.manualTick: don't run an internal timer; the caller drives tick().
   * Meeting mode drives it from the audio pipeline because Chrome throttles
   * timers in background/occluded windows while a call app is in front.
   */
  async function start(video, callback, opts = {}) {
    stop();
    videoEl = video;
    onState = callback;
    let landmarker;
    try {
      landmarker = await loadLandmarker();
    } catch (err) {
      console.warn('Gaze tracking unavailable (MediaPipe failed to load):', err);
      return false;
    }
    if (videoEl !== video) return false;  // stopped/restarted while loading
    landmarkerRef = landmarker;
    if (!opts.manualTick) timer = setInterval(tick, DETECT_INTERVAL_MS);
    return true;
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    landmarkerRef = null;
    lastDetectAt = 0;
    videoEl = null;
    onState = null;
    recent = [];
    current = null;
    lastSentAt = 0;
    lastVideoTime = -1;
    pitchSamples = [];
    pitchBaseline = null;
  }

  window.GazeTracker = { start, stop, tick, preload: () => loadLandmarker().catch(() => {}) };
})();
