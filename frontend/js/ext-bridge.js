/**
 * Pitchlight — link to the Pitchlight Chrome extension (extension/ in the repo).
 *
 * The extension captures the meeting tab's audio (no "Choose meeting audio" dialog) and its
 * bridge content script posts it here as 100 ms chunks of 16 kHz mono 16-bit PCM. This file
 * turns those chunks back into a MediaStream so meeting.js can treat it exactly like a
 * shared tab: same leveller, meters, talk-time and transcription path.
 *
 * window.PLExt
 *   state            { installed, capturing, title, host, platform, platformName, playback, reason, version }
 *   onChange(fn)     called with a copy of state whenever it changes
 *   openStream()     → MediaStream of the meeting audio. Call once the meeting starts (after a click).
 *   close()          drop the stream (stopCapture does this through meetingEnded)
 *   stopAudio()      ask the extension to stop listening
 *   meetingEnded()   tell the extension the meeting is over (it stops listening) and close
 */
(function () {
  'use strict';

  const FROM_EXT = 'pitchlight-ext';
  const FROM_PAGE = 'pitchlight-page';
  const RATE = 16000;
  const LEAD_S = 0.15;     // jitter buffer: chunks arrive through extension messaging, not a clock
  const MAX_AHEAD_S = 1.0; // if we fall this far behind, drop the backlog instead of adding delay

  const state = {
    installed: false, capturing: false, title: '', host: '', platform: '', platformName: '',
    playback: '', reason: '', version: '',
  };
  const listeners = [];
  let ctx = null;
  let dest = null;
  let nextTime = 0;

  function toExtension(type) {
    window.postMessage({ source: FROM_PAGE, type }, location.origin);
  }

  function emit() {
    const copy = { ...state };
    listeners.forEach((fn) => { try { fn(copy); } catch (err) { console.error(err); } });
  }

  function play(samples, rate) {
    if (!ctx || !dest || ctx.state !== 'running' || !samples.length) return;
    const buf = ctx.createBuffer(1, samples.length, rate || RATE);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < samples.length; i++) ch[i] = samples[i] / 32768;
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(dest);
    const now = ctx.currentTime;
    if (nextTime < now + 0.02 || nextTime > now + MAX_AHEAD_S) nextTime = now + LEAD_S;
    src.start(nextTime);
    nextTime += buf.duration;
  }

  window.addEventListener('message', (e) => {
    if (e.source !== window || e.origin !== location.origin) return;
    const d = e.data;
    if (!d || d.source !== FROM_EXT) return;
    if (d.type === 'pcm') {
      if (d.pcm instanceof ArrayBuffer) play(new Int16Array(d.pcm), d.rate);
      return;
    }
    if (d.type === 'hello') {
      const changed = !state.installed;
      state.installed = true;
      state.version = d.version || state.version;
      if (changed) emit();
      return;
    }
    if (d.type === 'status') {
      state.installed = d.reason !== 'extension_reloaded';
      state.capturing = !!d.capturing;
      state.title = d.title || '';
      state.host = d.host || '';
      state.platform = d.platform || '';
      state.platformName = d.platformName || '';
      state.playback = d.playback || '';
      state.reason = d.reason || '';
      emit();
    }
  });

  async function openStream() {
    if (!ctx) {
      ctx = new AudioContext({ sampleRate: RATE });
      dest = ctx.createMediaStreamDestination();
    }
    if (ctx.state !== 'running') { try { await ctx.resume(); } catch (_) { /* needs a click first */ } }
    nextTime = 0;
    return dest.stream;
  }

  function close() {
    if (ctx) ctx.close().catch(() => {});
    ctx = null;
    dest = null;
  }

  window.PLExt = {
    get state() { return { ...state }; },
    onChange(fn) { listeners.push(fn); },
    openStream,
    close,
    stopAudio() { toExtension('stop_audio'); },
    meetingStarted() { toExtension('meeting_started'); },
    meetingEnded() { toExtension('meeting_ended'); close(); },
  };

  toExtension('hello');   // the bridge answers with hello + the current capture status
})();
