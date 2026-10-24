// AudioWorkletProcessor: mic → 16 kHz Int16LE frames.
//  1. Anti-aliasing low-pass: 2nd-order Butterworth biquad at ~7 kHz, run at the input rate.
//  2. Fractional-step averaging decimator from `sampleRate` (usually 48000) to 16000.
//  3. Accumulate `frameSamples` samples (processorOptions; default 640 = 40 ms), convert to
//     Int16 and post the ArrayBuffer (transferred) to the main thread.
/* global sampleRate, registerProcessor, AudioWorkletProcessor */

const TARGET_RATE = 16000;
const DEFAULT_FRAME = 640;

class MicDownsampler extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opt = (options && options.processorOptions) || {};
    const fs = Number(opt.frameSamples);
    this.frame = Number.isFinite(fs) && fs >= 80 && fs <= 4800 ? Math.round(fs) : DEFAULT_FRAME;

    // Decimator state.
    this.ratio = sampleRate / TARGET_RATE; // 3.0 for 48k, 2.75625 for 44.1k
    this.pos = 0;
    this.sum = 0;
    this.count = 0;
    this.out = new Int16Array(this.frame);
    this.idx = 0;

    // Butterworth low-pass (RBJ cookbook, Q = 1/sqrt(2)). Keep the cutoff below input Nyquist.
    const f0 = Math.min(7000, 0.45 * sampleRate);
    const w0 = (2 * Math.PI * f0) / sampleRate;
    const cosw = Math.cos(w0);
    const alpha = Math.sin(w0) / (2 * Math.SQRT1_2);
    const a0 = 1 + alpha;
    this.b0 = (1 - cosw) / 2 / a0;
    this.b1 = (1 - cosw) / a0;
    this.b2 = (1 - cosw) / 2 / a0;
    this.a1 = (-2 * cosw) / a0;
    this.a2 = (1 - alpha) / a0;
    // Transposed direct form II state.
    this.z1 = 0;
    this.z2 = 0;
  }

  process(inputs) {
    const input = inputs[0];
    const ch = input && input[0];
    if (!ch) return true;

    const { b0, b1, b2, a1, a2, ratio } = this;
    let z1 = this.z1;
    let z2 = this.z2;

    for (let i = 0; i < ch.length; i++) {
      // 1. low-pass at the input rate
      const x = ch[i];
      const y = b0 * x + z1;
      z1 = b1 * x - a1 * y + z2;
      z2 = b2 * x - a2 * y;

      // 2. fractional-step averaging decimator
      this.sum += y;
      this.count++;
      this.pos += 1;
      if (this.pos >= ratio) {
        this.pos -= ratio;
        let s = this.count ? this.sum / this.count : 0;
        this.sum = 0;
        this.count = 0;
        if (s > 1) s = 1;
        else if (s < -1) s = -1;
        this.out[this.idx++] = s < 0 ? s * 0x8000 : s * 0x7fff;

        // 3. emit a full frame
        if (this.idx === this.frame) {
          const buf = this.out.buffer;
          this.port.postMessage(buf, [buf]);
          this.out = new Int16Array(this.frame);
          this.idx = 0;
        }
      }
    }

    // Flush denormals so a silent mic doesn't slow the filter down.
    this.z1 = Math.abs(z1) < 1e-20 ? 0 : z1;
    this.z2 = Math.abs(z2) < 1e-20 ? 0 : z2;
    return true;
  }
}

registerProcessor('mic-downsampler', MicDownsampler);
