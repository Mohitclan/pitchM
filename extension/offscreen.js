/**
 * Pitchlight extension — offscreen document.
 *
 * Turns the tab-capture stream id into audio, keeps the meeting audible (Chrome mutes a
 * captured tab unless the audio is played back) and sends 100 ms chunks of 16 kHz mono
 * 16-bit PCM to the service worker, which relays them to the Pitchlight page.
 */
'use strict';

const SAMPLE_RATE = 16000;
const CHUNK_SAMPLES = 1600;   // 100 ms, the same chunk size the meeting page uses

let media = null;
let playCtx = null;
let procCtx = null;
let seq = 0;

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== 'offscreen') return undefined;
  if (msg.type === 'start') {
    start(msg.streamId).then(
      (playback) => sendResponse({ ok: true, playback }),
      (err) => { stop(); sendResponse({ ok: false, error: String(err?.message || err) }); },
    );
    return true;
  }
  if (msg.type === 'stop') {
    stop();
    sendResponse({ ok: true });
  }
  return undefined;
});

async function start(streamId) {
  stop();
  media = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
    video: false,
  });

  // Keep playing the meeting to the user.
  playCtx = new AudioContext();
  playCtx.createMediaStreamSource(media).connect(playCtx.destination);
  if (playCtx.state !== 'running') { try { await playCtx.resume(); } catch (_) { /* reported below */ } }

  // Downmix + resample to 16 kHz and cut into 100 ms chunks.
  procCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
  await procCtx.audioWorklet.addModule('pcm-worklet.js');
  const source = procCtx.createMediaStreamSource(media);
  const node = new AudioWorkletNode(procCtx, 'pitchlight-pcm', { processorOptions: { chunkSamples: CHUNK_SAMPLES } });
  node.port.onmessage = (e) => {
    chrome.runtime.sendMessage({ target: 'sw', type: 'pcm', rate: SAMPLE_RATE, seq: seq++, data: toBase64(e.data) })
      .catch(() => { /* worker restarting: drop this chunk */ });
  };
  const sink = procCtx.createGain();   // keeps the worklet in a running graph without playing it twice
  sink.gain.value = 0;
  source.connect(node);
  node.connect(sink);
  sink.connect(procCtx.destination);

  const track = media.getAudioTracks()[0];
  track.addEventListener('ended', () => {
    stop();
    chrome.runtime.sendMessage({ target: 'sw', type: 'ended' }).catch(() => {});
  });
  return playCtx.state;
}

function stop() {
  if (media) { media.getTracks().forEach((t) => t.stop()); media = null; }
  if (playCtx) { playCtx.close().catch(() => {}); playCtx = null; }
  if (procCtx) { procCtx.close().catch(() => {}); procCtx = null; }
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x2000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x2000));
  return btoa(bin);
}
