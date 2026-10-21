/**
 * Pitchlight — audio levelling for speech-to-text.
 *
 * Every audio stream that is transcribed (your mic, the meeting's audio) goes through:
 *   high-pass filter (cuts desk thumps / AC rumble below 85 Hz)
 *   → leveller worklet: smooth automatic gain toward a target speech level, so a
 *     quiet aside gets boosted and a loud burst gets pulled down, plus a limiter so
 *     loud speech never clips. Gain is only raised when there is real signal above
 *     the noise floor, so background hiss is not amplified into "speech".
 *
 * The worklet posts 100 ms chunks: { pcm: Float32Array (levelled), raw: rms before
 * levelling, gain }. `raw` is what voice-activity logic should use.
 */
(function () {
  'use strict';

  const WORKLET_CODE = `
    class PMLeveler extends AudioWorkletProcessor {
      constructor(options) {
        super();
        const o = (options && options.processorOptions) || {};
        this.chunk = o.chunkSamples || 1600;
        this.target = o.targetRms || 0.07;     // speech level we aim for
        this.maxGain = o.maxGain || 6;         // never boost more than this (≈ +15.5 dB)
        this.minGain = o.minGain || 0.35;      // pull loud bursts down to this at most
        this.floor = o.noiseFloor || 0.008;    // below this envelope we don't boost
        this.ceiling = 0.92;                   // limiter threshold
        this.env = 0;
        this.gain = 1;
        this.buf = new Float32Array(this.chunk);
        this.n = 0;
        this.rawSq = 0;
      }
      process(inputs) {
        const ch = inputs[0] && inputs[0][0];
        if (!ch) return true;
        // Frame energy → envelope (fast attack, slow release).
        let sq = 0;
        for (let i = 0; i < ch.length; i++) sq += ch[i] * ch[i];
        const rms = Math.sqrt(sq / ch.length);
        this.env += (rms > this.env ? 0.35 : 0.04) * (rms - this.env);
        // Desired gain. Only boost while this frame has real signal above the noise floor;
        // in pauses relax toward unity so background hiss is never amplified.
        let want = rms > this.floor && this.env > this.floor ? this.target / this.env : 1;
        want = Math.max(this.minGain, Math.min(this.maxGain, want));
        for (let i = 0; i < ch.length; i++) {
          // Smooth gain changes (no pumping): faster when reducing, slower when boosting.
          this.gain += (want < this.gain ? 0.02 : 0.002) * (want - this.gain);
          let y = ch[i] * this.gain;
          const a = Math.abs(y);
          if (a > this.ceiling) y = Math.sign(y) * (this.ceiling + (1 - this.ceiling) * Math.tanh((a - this.ceiling) / (1 - this.ceiling)));
          this.buf[this.n] = y;
          this.rawSq += ch[i] * ch[i];
          this.n++;
          if (this.n === this.chunk) {
            const out = this.buf;
            this.port.postMessage({ pcm: out, raw: Math.sqrt(this.rawSq / this.chunk), gain: this.gain }, [out.buffer]);
            this.buf = new Float32Array(this.chunk);
            this.n = 0;
            this.rawSq = 0;
          }
        }
        return true;
      }
    }
    registerProcessor('pm-leveler', PMLeveler);`;

  const loaded = new WeakSet();

  async function ensureWorklet(ctx) {
    if (loaded.has(ctx)) return;
    const url = URL.createObjectURL(new Blob([WORKLET_CODE], { type: 'application/javascript' }));
    try { await ctx.audioWorklet.addModule(url); } finally { URL.revokeObjectURL(url); }
    loaded.add(ctx);
  }

  /**
   * Build the levelling chain for one stream and return the worklet node.
   * onChunk({ pcm, raw, gain }) is called every chunkSamples samples.
   * opts.kind: 'mic' (default) or 'meeting' — meeting audio is already clean digital
   * audio, so it gets a little less boost.
   */
  async function createLeveler(ctx, mediaStream, onChunk, opts = {}) {
    await ensureWorklet(ctx);
    const src = ctx.createMediaStreamSource(new MediaStream(mediaStream.getAudioTracks()));
    const hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = 85;
    hp.Q.value = 0.7;
    const node = new AudioWorkletNode(ctx, 'pm-leveler', {
      processorOptions: {
        chunkSamples: opts.chunkSamples || 1600,
        targetRms: opts.kind === 'meeting' ? 0.06 : 0.07,
        maxGain: opts.kind === 'meeting' ? 5 : 6,
        noiseFloor: opts.kind === 'meeting' ? 0.004 : 0.008,
      },
    });
    node.port.onmessage = (e) => onChunk(e.data);
    // Keep the worklet in a live graph without playing anything out loud.
    const sink = ctx.createGain();
    sink.gain.value = 0;
    src.connect(hp);
    hp.connect(node);
    node.connect(sink);
    sink.connect(ctx.destination);
    node.pmSource = src;
    return node;
  }

  /** getUserMedia audio constraints tuned for speech (browser AGC + noise suppression). */
  const MIC_CONSTRAINTS = {
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };

  window.PMAudio = { createLeveler, MIC_CONSTRAINTS };
})();
