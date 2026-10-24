import { useCallback, useEffect, useRef, useState } from 'react';
import { useCallAudio } from '../audio/useCallAudio';
import { playTone, sayOut } from '../audio/speech';
import { useWebSpeech } from '../audio/useWebSpeech';
import { CallBubble } from '../components/CallBubble';
import { MockSay } from '../components/MockSay';
import { SubtitleStrip, useSubtitles } from '../components/SubtitleStrip';
import { PhoneFrame, StatusBar } from '../components/PhoneFrame';
import { PhoneIcon } from '../components/icons';
import type { PhoneServerMsg, Tone, WhisperMsg } from '../lib/types';
import { useCallTimer } from '../lib/useCallTimer';
import { usePhone } from '../lib/usePhone';

const QUICK_LINES = [
  'Oh hello dear, yes, how can I help?',
  "Oh my, let me find it... it's four, seven, two, nine...",
  "Okay, I won't tell anyone.",
  'Guardian, is this real?',
  'Guardian, hang up for me.',
];

const GLOW_FOR: Record<Tone, 'caution' | 'alert'> = { caution: 'caution', alert: 'alert', pii: 'alert', end: 'alert' };

function vibrate(pattern: number[]) {
  try {
    if (typeof navigator.vibrate === 'function') navigator.vibrate(pattern);
  } catch {
    /* iOS / unsupported */
  }
}

export default function Granny() {
  const [glow, setGlow] = useState<'none' | 'caution' | 'alert'>('none');
  const [endedLocal, setEndedLocal] = useState(false);
  const [tray, setTray] = useState(false);
  const [sttKey, setSttKey] = useState(0);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const sendRef = useRef<(m: { type: 'whisper_done'; id: string }) => void>(() => undefined);
  const ctxRef = useRef<AudioContext | null>(null);
  const centerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState(240);

  // Fit the bubble to the space between the caller name and the button.
  useEffect(() => {
    const el = centerRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect;
      setSize(Math.max(120, Math.min(320, Math.floor(Math.min(width, height) * 0.86))));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const duckRef = useRef<(on: boolean) => void>(() => undefined);
  const subs = useSubtitles();

  useEffect(() => {
    document.title = 'Phone';
  }, []);

  // Whispers play strictly one at a time: tone → voice → whisper_done.
  const playWhisper = useCallback((w: WhisperMsg) => {
    queue.current = queue.current
      .then(async () => {
        setGlow(GLOW_FOR[w.tone] ?? 'caution');
        duckRef.current(true);
        try {
          await playTone(ctxRef.current, w.tone);
          await sayOut(w.text, w.audio_b64, w.mime);
        } finally {
          duckRef.current(false);
          setGlow('none');
          sendRef.current({ type: 'whisper_done', id: w.id });
        }
      })
      .catch(() => undefined);
  }, []);

  const onMsg = useCallback(
    (m: PhoneServerMsg) => {
      switch (m.type) {
        case 'whisper':
          playWhisper(m);
          break;
        case 'vibrate':
          vibrate(m.pattern);
          break;
        case 'subtitle':
          subs.push(m);
          break;
        case 'stt_restart':
          setSttKey((k) => k + 1);
          break;
        case 'call_ended':
          setEndedLocal(true);
          setTray(false);
          break;
        case 'state':
          if (m.call === 'ringing') {
            setEndedLocal(false);
            subs.clear();
            vibrate([400, 200, 400]);
          }
          if (m.call === 'active') setEndedLocal(false);
          break;
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [playWhisper],
  );

  const { connected, replaced, state, send, sock } = usePhone('granny', onMsg);
  sendRef.current = send;
  const call = state?.call ?? 'idle';
  const active = call === 'active' && !endedLocal;
  const ringing = call === 'ringing' && !endedLocal;
  const mode = state?.stt_mode ?? 'mock';
  const audio = useCallAudio(sock, active, false, state?.audio_frame_ms ?? 40);
  ctxRef.current = audio.ctx;
  duckRef.current = audio.duck;
  useWebSpeech(sock, active && (mode === 'browser' || mode === 'hybrid'), sttKey);
  const timer = useCallTimer(active);

  useEffect(() => {
    if (endedLocal || call === 'ended') audio.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endedLocal, call]);

  useEffect(() => {
    if (!ringing) return;
    const id = window.setInterval(() => vibrate([400, 200, 400]), 2500);
    return () => window.clearInterval(id);
  }, [ringing]);

  const answer = () => {
    audio.start(); // AudioContext + resume inside the gesture
    try {
      const u = new SpeechSynthesisUtterance(' ');
      u.volume = 0;
      window.speechSynthesis?.speak(u); // prime TTS within the gesture (mobile Chrome)
    } catch {
      /* ignore */
    }
    send({ type: 'answer' });
  };
  const hangup = () => {
    send({ type: 'hangup' });
    setEndedLocal(true);
    setTray(false);
  };

  const now = new Date();

  return (
    <div className="page page--granny">
      <div className="phone-col">
      <PhoneFrame variant="warm">
        <StatusBar />
        <div className="gcall">
          <div className="gcall__who">
            <div className="gcall__name">{ringing || active ? 'Unknown caller' : now.toLocaleDateString(undefined, { weekday: 'long' })}</div>
            <div className="gcall__sub">
              {ringing ? 'incoming call' : active ? timer : endedLocal || call === 'ended' ? 'call ended' : `${now.getHours() % 12 || 12}:${String(now.getMinutes()).padStart(2, '0')}`}
            </div>
          </div>

          <div className="gcall__center" ref={centerRef}>
            <CallBubble analysers={active ? [audio.remoteAnalyser, audio.micAnalyser] : []} glow={active ? glow : 'none'} size={size} />
          </div>

          <div className="gcall__action">
            {ringing ? (
              <button className="gbtn gbtn--answer" onClick={answer} aria-label="Answer">
                <PhoneIcon />
                <span>Answer</span>
              </button>
            ) : active ? (
              <button className="gbtn gbtn--hangup" onClick={hangup} aria-label="Hang up">
                <PhoneIcon down />
                <span>Hang up</span>
              </button>
            ) : (
              <div className="gcall__rest">{connected ? '' : replaced ? 'Open on another device' : 'connecting…'}</div>
            )}
            {audio.micError && active && <div className="hint">Microphone: {audio.micError}</div>}
          </div>

          {/* Mock mode only: a tiny tucked-away tray so the demo can speak for granny. */}
          {mode === 'mock' && active && (
            <>
              <button className="gtray-toggle" onClick={() => setTray((t) => !t)} aria-label="Lines">
                ···
              </button>
              <div className={`tray tray--warm ${tray ? 'is-open' : ''}`}>
                <div className="tray__grip" onClick={() => setTray(false)} />
                <MockSay variant="warm" lines={QUICK_LINES} onSay={(text) => send({ type: 'say', text })} />
              </div>
            </>
          )}
        </div>
      </PhoneFrame>
      <SubtitleStrip lines={subs.lines} now={subs.now} tone="warm" page="granny" />
      </div>
    </div>
  );
}
