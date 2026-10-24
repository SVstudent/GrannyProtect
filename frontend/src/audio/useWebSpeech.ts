// Browser STT via webkitSpeechRecognition. Sends {type:"stt"} partials (cumulative interim text
// of the current result) and finals. Auto-restarts on end while enabled.
import { useEffect, useRef, useState } from 'react';
import type { ReconnectingWS } from '../lib/ws';

export function speechRecognitionAvailable(): boolean {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

/** `restartKey`: bump it (e.g. on a server `stt_restart`) to tear down and restart recognition. */
export function useWebSpeech(ws: ReconnectingWS | null, enabled: boolean, restartKey = 0) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  useEffect(() => {
    if (!enabled || !ws) return;
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Ctor) {
      setError('Speech recognition not supported in this browser');
      return;
    }
    let stopped = false;
    let restartTimer: number | undefined;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = 'en-US';

    rec.onresult = (ev: SpeechRecognitionEvent) => {
      let interim = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        const text = r[0]?.transcript ?? '';
        if (r.isFinal) {
          const t = text.trim();
          if (t) ws.send({ type: 'stt', final: true, text: t });
        } else {
          interim += text;
        }
      }
      const t = interim.trim();
      if (t) ws.send({ type: 'stt', final: false, text: t });
    };
    rec.onerror = (ev: SpeechRecognitionErrorEvent) => {
      if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
        setError('Speech recognition blocked');
        stopped = true;
      }
    };
    rec.onstart = () => setListening(true);
    rec.onend = () => {
      setListening(false);
      if (stopped || !enabledRef.current) return;
      restartTimer = window.setTimeout(() => {
        if (stopped || !enabledRef.current) return;
        try {
          rec.start();
        } catch {
          /* already started */
        }
      }, 250);
    };
    try {
      rec.start();
      setError(null);
    } catch {
      /* ignore */
    }
    return () => {
      stopped = true;
      window.clearTimeout(restartTimer);
      try {
        rec.abort();
      } catch {
        /* ignore */
      }
      setListening(false);
    };
  }, [enabled, ws, restartKey]);

  return { listening, error, supported: speechRecognitionAvailable() };
}
