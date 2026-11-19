/**
 * AudioWorklet: mixes the captured tab audio down to mono and posts fixed-size chunks of
 * 16-bit PCM (the context runs at 16 kHz, so Chrome has already resampled the input).
 */
class PitchlightPcm extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.size = options.processorOptions?.chunkSamples || 1600;
    this.buf = new Int16Array(this.size);
    this.pos = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input.length) return true;
    const channels = input.length;
    const frames = input[0].length;
    for (let i = 0; i < frames; i++) {
      let v = 0;
      for (let c = 0; c < channels; c++) v += input[c][i];
      v /= channels;
      if (v > 1) v = 1; else if (v < -1) v = -1;
      this.buf[this.pos++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.pos === this.size) {
        this.port.postMessage(this.buf.buffer, [this.buf.buffer]);
        this.buf = new Int16Array(this.size);
        this.pos = 0;
      }
    }
    return true;
  }
}

registerProcessor('pitchlight-pcm', PitchlightPcm);
