// Full-duplex call audio over the phone websocket.
// Mic -> AudioWorklet (7 kHz anti-alias LPF, 48k -> 16k Int16, audio_frame_ms frames) -> ws binary.
// ws binary (16k Int16) -> AudioBuffer scheduled on an adaptive jitter buffer -> Gain -> Analyser -> speakers.
import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReconnectingWS } from '../lib/ws';

const WORKLET_URL = '/mic-worklet.js'; // served from public/, survives Vite builds untouched
// Adaptive jitter buffer (seconds). Wi-Fi delivers 40 ms frames with 30-80 ms of jitter, so a 50-60 ms cushion
// ran dry often (each underrun is an audible gap). Start with more headroom, grow faster after a gap, and give
// it back slowly: smooth audio for only ~10-20 ms more delay, and the cushion only grows when the network needs it.
const LEAD_START = 0.08;
const LEAD_MIN = 0.06;
const LEAD_MAX = 0.3;
const LEAD_GROW = 0.04; // per underrun
const LEAD_SHRINK = 0.005; // per quiet window
const QUIET_WINDOW = 8; // seconds without underrun before shrinking
const MAX_AHEAD = 0.6; // last resort: snap back if the cursor is more than lead + this ahead
const RAMP = 0.008; // fade-in at a cursor jump, avoids clicks
// Seamless catch-up: when a network burst leaves more queued than the cushion needs, play ~6% faster until the
// backlog is gone (inaudible for speech) instead of throwing audio away. The hard snap is a last resort only.
const CATCHUP_OVER = 0.06; // seconds above the cushion before speeding up
const CATCHUP_RATE = 1.06;

declare global {
  interface Window {
    __audioStats?: { lead: number; underruns: number };
  }
}

export interface CallAudio {
  /** Call synchronously inside a click handler (dial / answer). */
  start: () => void;
  stop: () => void;
  duck: (on: boolean) => void;
  ctx: AudioContext | null;
  micAnalyser: AnalyserNode | null;
  remoteAnalyser: AnalyserNode | null;
  micError: string | null;
  micReady: boolean;
}

/**
 * @param ws      the phone socket wrapper
 * @param active  true while the call is active (frames only flow then)
 * @param muted   gate outgoing frames (caller mute button)
 * @param frameMs mic chunk size from `state.audio_frame_ms` (applied when the mic starts)
 */
export function useCallAudio(ws: ReconnectingWS | null, active: boolean, muted = false, frameMs = 40): CallAudio {
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nodeRef = useRef<AudioWorkletNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const cursorRef = useRef(0);
  const leadRef = useRef(LEAD_START);
  const underrunsRef = useRef(0);
  const lastAdjustRef = useRef(0); // ctx time of the last underrun or shrink
  const sourcesRef = useRef(new Set<AudioBufferSourceNode>());
  const frameMsRef = useRef(frameMs);
  frameMsRef.current = frameMs;
  const moduleRef = useRef<{ ctx: AudioContext; p: Promise<void> } | null>(null);
  const activeRef = useRef(active);
  const mutedRef = useRef(muted);
  const wsRef = useRef(ws);
  const [ctx, setCtx] = useState<AudioContext | null>(null);
  const [micAnalyser, setMicAnalyser] = useState<AnalyserNode | null>(null);
  const [remoteAnalyser, setRemoteAnalyser] = useState<AnalyserNode | null>(null);
  const [micError, setMicError] = useState<string | null>(null);
  const [micReady, setMicReady] = useState(false);

  activeRef.current = active;
  mutedRef.current = muted;
  wsRef.current = ws;

  function resetPlayback() {
    sourcesRef.current.forEach((s) => {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    });
    sourcesRef.current.clear();
    cursorRef.current = 0;
    leadRef.current = LEAD_START;
    underrunsRef.current = 0;
  }

  const ensureContext = useCallback((): AudioContext => {
    let c = ctxRef.current;
    if (!c || c.state === 'closed') {
      const AC: typeof AudioContext =
        window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      c = new AC({ latencyHint: 'interactive' });
      ctxRef.current = c;
      const gain = c.createGain();
      gain.gain.value = 1;
      const an = c.createAnalyser();
      an.fftSize = 1024;
      an.smoothingTimeConstant = 0.6;
      gain.connect(an);
      an.connect(c.destination);
      gainRef.current = gain;
      cursorRef.current = 0;
      setCtx(c);
      setRemoteAnalyser(an);
    }
    void c.resume();
    return c;
  }, []);

  const startMic = useCallback(async (c: AudioContext) => {
    if (streamRef.current) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      setMicError(window.isSecureContext ? 'Microphone not available' : 'Microphone needs HTTPS');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        video: false,
      });
      streamRef.current = stream;
      if (!moduleRef.current || moduleRef.current.ctx !== c) moduleRef.current = { ctx: c, p: c.audioWorklet.addModule(WORKLET_URL) };
      await moduleRef.current.p;
      const src = c.createMediaStreamSource(stream);
      const ms = Number.isFinite(frameMsRef.current) && frameMsRef.current > 0 ? frameMsRef.current : 40;
      const node = new AudioWorkletNode(c, 'mic-downsampler', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        channelCount: 1,
        processorOptions: { frameSamples: Math.round((16000 * ms) / 1000) },
      });
      node.port.onmessage = (ev: MessageEvent<ArrayBuffer>) => {
        if (!activeRef.current || mutedRef.current) return;
        wsRef.current?.send(ev.data);
      };
      // Keep the worklet pulled by routing it to a silent gain.
      const sink = c.createGain();
      sink.gain.value = 0;
      src.connect(node);
      node.connect(sink);
      sink.connect(c.destination);
      // Separate analyser for the waveform (never routed to the speakers).
      const an = c.createAnalyser();
      an.fftSize = 1024;
      an.smoothingTimeConstant = 0.6;
      src.connect(an);
      nodeRef.current = node;
      setMicAnalyser(an);
      setMicReady(true);
      setMicError(null);
    } catch (e) {
      setMicError(e instanceof Error ? e.message : 'Microphone blocked');
    }
  }, []);

  const start = useCallback(() => {
    const c = ensureContext(); // synchronous, inside the gesture
    void startMic(c);
  }, [ensureContext, startMic]);

  /** Duck the remote voice (e.g. while a whisper plays). */
  const duck = useCallback((on: boolean) => {
    const c = ctxRef.current;
    const g = gainRef.current;
    if (!c || !g) return;
    g.gain.cancelScheduledValues(c.currentTime);
    g.gain.setTargetAtTime(on ? 0.25 : 1, c.currentTime, 0.08);
  }, []);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    nodeRef.current?.disconnect();
    nodeRef.current = null;
    setMicAnalyser(null);
    setMicReady(false);
    resetPlayback();
  }, []);

  // Incoming audio playback through an adaptive jitter buffer.
  useEffect(() => {
    if (!ws) return;
    return ws.onBinary((buf) => {
      const c = ctxRef.current;
      const gain = gainRef.current;
      if (!c || !gain || !activeRef.current || c.state !== 'running') return;
      const pcm = new Int16Array(buf);
      const n = pcm.length;
      if (!n) return;
      const ab = c.createBuffer(1, n, 16000);
      const ch = ab.getChannelData(0);
      for (let i = 0; i < n; i++) ch[i] = pcm[i] / 32768;

      const now = c.currentTime;
      let cur = cursorRef.current;
      let jumped = false;
      if (cur === 0) {
        // First chunk of a call.
        cur = now + leadRef.current;
        jumped = true;
        lastAdjustRef.current = now;
      } else if (cur < now) {
        // Underrun: playback ran dry. Grow the buffer and restart ahead of now.
        leadRef.current = Math.min(LEAD_MAX, leadRef.current + LEAD_GROW);
        underrunsRef.current++;
        lastAdjustRef.current = now;
        cur = now + leadRef.current;
        jumped = true;
      } else if (cur - now > leadRef.current + MAX_AHEAD) {
        // Backlog: drop queued audio so delay never accumulates.
        sourcesRef.current.forEach((s) => {
          try {
            s.stop();
          } catch {
            /* already stopped */
          }
        });
        sourcesRef.current.clear();
        cur = now + leadRef.current;
        jumped = true;
      }
      // Quiet for a while: tighten the buffer to cut latency.
      if (now - lastAdjustRef.current > QUIET_WINDOW) {
        leadRef.current = Math.max(LEAD_MIN, leadRef.current - LEAD_SHRINK);
        lastAdjustRef.current = now;
      }

      const node = c.createBufferSource();
      node.buffer = ab;
      const rate = !jumped && cur - now > leadRef.current + CATCHUP_OVER ? CATCHUP_RATE : 1;
      if (rate !== 1) node.playbackRate.value = rate;
      if (jumped) {
        // Short fade-in only at discontinuities, to avoid a click.
        const g = c.createGain();
        g.gain.setValueAtTime(0, cur);
        g.gain.linearRampToValueAtTime(1, cur + RAMP);
        node.connect(g);
        g.connect(gain);
      } else {
        node.connect(gain);
      }
      node.onended = () => sourcesRef.current.delete(node);
      sourcesRef.current.add(node);
      node.start(cur);
      cursorRef.current = cur + ab.duration / rate;
      window.__audioStats = { lead: +leadRef.current.toFixed(3), underruns: underrunsRef.current };
    });
  }, [ws]);

  // Tear down on unmount.
  useEffect(
    () => () => {
      stop();
      void ctxRef.current?.close().catch(() => undefined);
      ctxRef.current = null;
    },
    [stop],
  );

  return { start, stop, duck, ctx, micAnalyser, remoteAnalyser, micError, micReady };
}
