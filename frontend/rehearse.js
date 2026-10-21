/**
 * Pitchlight — shared rehearsal helpers (Meeting Mode pre-call rehearsal, practice retries).
 *
 * - listen(): streams the mic to /ws?kind=rehearsal and returns finished lines of speech
 *   (the server's live transcriber; nothing is coached or saved)
 * - speak(): reads text aloud with the browser's own voice (free, on-device)
 * - speechStats() / keywordMatch(): instant checks on a retry, no AI call
 */
(function () {
  'use strict';

  const T_AUDIO_IN = 0x01, T_STOP = 0x03;
  const FILLER_RE = /\b(um+|uh+|erm+|uhm+|you know|basically|literally|kind of|sort of|i mean)\b/gi;
  const STOP_WORDS = new Set(('a an the and or but if then so to of in on at for with from by as is are was were be been '
    + 'it its this that these those i we you they he she our your their my me us them will would can could should '
    + 'do does did have has had not no yes just very really about into than also more most').split(' '));

  function frame(type, buf) {
    const out = new Uint8Array(1 + buf.byteLength);
    out[0] = type;
    out.set(new Uint8Array(buf), 1);
    return out.buffer;
  }

  function toPcm16(f32) {
    const pcm = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const v = Math.max(-1, Math.min(1, f32[i]));
      pcm[i] = v < 0 ? v * 0x8000 : v * 0x7FFF;
    }
    return pcm.buffer;
  }

  /**
   * Start listening. opts: { userId, onText(text), onStatus(state, message), onLevel(rms) }.
   * Resolves to { stop(): Promise<void> } — stop() waits for the last sentence to come back.
   */
  async function listen(opts = {}) {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { ...(window.PMAudio?.MIC_CONSTRAINTS || {}), sampleRate: 16000 },
    });
    const url = new URL(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    url.searchParams.set('kind', 'rehearsal');
    if (opts.userId) url.searchParams.set('user', opts.userId);
    const token = (localStorage.getItem('pitchmirror_api_token') || '').trim();
    if (token) url.searchParams.set('token', token);

    const ws = new WebSocket(url.toString());
    ws.binaryType = 'arraybuffer';
    let ctx = null, node = null, finished = null;
    const done = new Promise((resolve) => { finished = resolve; });

    ws.onmessage = (ev) => {
      if (typeof ev.data !== 'string') return;
      let msg;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }
      if (msg.type === 'transcript' && msg.text) opts.onText?.(msg.text);
      else if (msg.type === 'stt_status') opts.onStatus?.(msg.state);
      else if (msg.type === 'status' && msg.state === 'error') opts.onStatus?.('failed', msg.message);
      else if (msg.type === 'rehearsal_done') finished();
    };
    ws.onclose = () => finished();

    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error('Could not connect to the speech service.'));
    });
    ctx = new AudioContext({ sampleRate: 16000 });
    node = await window.PMAudio.createLeveler(ctx, stream, (chunk) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(frame(T_AUDIO_IN, toPcm16(chunk.pcm)));
      opts.onLevel?.(chunk.raw);
    }, { kind: 'mic', chunkSamples: 1600 });

    function release() {
      if (node) { node.port.onmessage = null; node.pmSource?.disconnect(); node.disconnect(); node = null; }
      if (ctx) { ctx.close().catch(() => {}); ctx = null; }
      stream.getTracks().forEach((t) => t.stop());
    }

    return {
      /** Stop the mic and wait (up to 9 s) for the transcriber to return the last sentence. */
      async stop() {
        release();
        if (ws.readyState === WebSocket.OPEN) ws.send(new Uint8Array([T_STOP]).buffer);
        await Promise.race([done, new Promise((r) => setTimeout(r, 9000))]);
        try { ws.close(); } catch (_) { /* ignore */ }
      },
    };
  }

  let voice = null;
  function pickVoice() {
    if (voice || !window.speechSynthesis) return voice;
    const voices = speechSynthesis.getVoices();
    voice = voices.find((v) => /en[-_]/i.test(v.lang) && /natural|neural|premium|enhanced|samantha|google/i.test(v.name))
      || voices.find((v) => /^en/i.test(v.lang)) || null;
    return voice;
  }
  if (window.speechSynthesis) speechSynthesis.addEventListener?.('voiceschanged', () => { voice = null; pickVoice(); });

  /** Read text aloud. Resolves when finished (or immediately if the browser has no voice). */
  function speak(text, { rate = 1 } = {}) {
    return new Promise((resolve) => {
      if (!window.speechSynthesis || !text) { resolve(); return; }
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      const v = pickVoice();
      if (v) u.voice = v;
      u.rate = rate;
      u.onend = u.onerror = () => resolve();
      speechSynthesis.speak(u);
    });
  }

  function stopSpeaking() { window.speechSynthesis?.cancel(); }

  function words(text) { return String(text || '').toLowerCase().match(/[a-z0-9']+/g) || []; }

  function speechStats(text, seconds) {
    const n = words(text).length;
    return {
      words: n,
      fillers: (String(text || '').match(FILLER_RE) || []).length,
      wpm: seconds > 3 ? Math.round((n / seconds) * 60) : null,
    };
  }

  /** Share of the target's key words (no stop words) that the retry used. */
  function keywordMatch(target, said) {
    const key = [...new Set(words(target).filter((w) => w.length > 2 && !STOP_WORDS.has(w)))];
    if (!key.length) return null;
    const heard = new Set(words(said));
    const hit = key.filter((w) => heard.has(w));
    return { hit: hit.length, total: key.length, missing: key.filter((w) => !heard.has(w)).slice(0, 8) };
  }

  window.PMRehearse = { listen, speak, stopSpeaking, speechStats, keywordMatch };
})();
