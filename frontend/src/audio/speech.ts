// Output helpers: tones, mp3 clips, speechSynthesis. Every promise resolves (never rejects)
// and has a timeout so queues can never stall.
import type { Tone } from '../lib/types';

function withTimeout(p: Promise<void>, ms: number): Promise<void> {
  return Promise.race([p, new Promise<void>((r) => setTimeout(r, ms))]);
}

export function toDataUrl(b64: string, mime = 'audio/mpeg'): string {
  return b64.startsWith('data:') ? b64 : `data:${mime || 'audio/mpeg'};base64,${b64}`;
}

export function playClip(b64: string, mime?: string): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    let done = false;
    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      resolve(ok);
    };
    try {
      const a = new Audio(toDataUrl(b64, mime));
      a.onended = () => finish(true);
      a.onerror = () => finish(false);
      a.play().catch(() => finish(false));
      // Hard cap: 30 s, or duration + 1.5 s once known.
      const cap = setTimeout(() => finish(true), 30000);
      a.onloadedmetadata = () => {
        if (isFinite(a.duration) && a.duration > 0) {
          clearTimeout(cap);
          setTimeout(() => finish(true), a.duration * 1000 + 1500);
        }
      };
    } catch {
      finish(false);
    }
  });
}

// Fallback voice for lines without a studio clip: prefer neural/"natural" voices over the robotic defaults.
const VOICE_PREFERENCE = [
  /natural/i, // Edge/Windows neural voices ("Microsoft Aria Online (Natural)")
  /google us english/i, // Chrome desktop, Android
  /samantha \(enhanced\)|ava \(premium\)|ava \(enhanced\)|zoe|allison/i, // macOS/iOS premium voices
  /samantha|karen|moira|serena/i,
  /female/i,
];
function bestVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | undefined {
  const en = voices.filter((v) => /^en(-|_)/i.test(v.lang));
  for (const re of VOICE_PREFERENCE) {
    const v = en.find((x) => re.test(x.name));
    if (v) return v;
  }
  return en.find((v) => v.lang === 'en-US') ?? en[0];
}

export function speak(text: string, rate = 0.92): Promise<void> {
  const synth = window.speechSynthesis;
  if (!synth || !text) return Promise.resolve();
  const p = new Promise<void>((resolve) => {
    try {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'en-US';
      u.rate = rate;
      const voice = bestVoice(synth.getVoices());
      if (voice) u.voice = voice;
      u.onend = () => resolve();
      u.onerror = () => resolve();
      synth.cancel();
      synth.speak(u);
    } catch {
      resolve();
    }
  });
  // Chrome sometimes never fires onend: estimate ~ 14 chars/s at rate 1.
  return withTimeout(p, 2500 + (text.length / (14 * rate)) * 1000);
}

/** Play audio_b64 if present, else speechSynthesis. */
export async function sayOut(text: string, audio_b64: string | null, mime?: string): Promise<void> {
  if (audio_b64) {
    const ok = await playClip(audio_b64, mime);
    if (ok) return;
  }
  await speak(text);
}

// ---- Tones ----
function beep(ctx: AudioContext, t0: number, freq: number, dur: number, peak: number, type: OscillatorType = 'sine', endFreq?: number) {
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, t0);
  if (endFreq) o.frequency.exponentialRampToValueAtTime(endFreq, t0 + dur);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(peak, t0 + 0.012);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  o.connect(g);
  g.connect(ctx.destination);
  o.start(t0);
  o.stop(t0 + dur + 0.02);
}

/** Distinct short cue per tone. Resolves when the tone has finished. */
export function playTone(ctx: AudioContext | null, tone: Tone): Promise<void> {
  if (!ctx || ctx.state === 'closed') return Promise.resolve();
  void ctx.resume();
  const t = ctx.currentTime + 0.03;
  let total = 0.5;
  switch (tone) {
    case 'caution': // soft single chime
      beep(ctx, t, 660, 0.7, 0.18);
      beep(ctx, t, 1320, 0.5, 0.05);
      total = 0.75;
      break;
    case 'alert': // two higher beeps
      beep(ctx, t, 1046, 0.16, 0.22, 'triangle');
      beep(ctx, t + 0.22, 1046, 0.16, 0.22, 'triangle');
      total = 0.45;
      break;
    case 'pii': // quick double tick
      beep(ctx, t, 1800, 0.035, 0.25, 'square');
      beep(ctx, t + 0.09, 1800, 0.035, 0.25, 'square');
      total = 0.2;
      break;
    case 'end': // descending chime
      beep(ctx, t, 784, 0.35, 0.18);
      beep(ctx, t + 0.18, 659, 0.35, 0.18);
      beep(ctx, t + 0.36, 523, 0.6, 0.18);
      total = 1.0;
      break;
  }
  return new Promise((r) => setTimeout(r, total * 1000 + 60));
}
