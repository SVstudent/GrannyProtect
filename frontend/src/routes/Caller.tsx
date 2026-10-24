import { useCallback, useEffect, useRef, useState } from 'react';
import { useCallAudio } from '../audio/useCallAudio';
import { sayOut } from '../audio/speech';
import { useWebSpeech } from '../audio/useWebSpeech';
import { MockSay } from '../components/MockSay';
import { SubtitleStrip, useSubtitles } from '../components/SubtitleStrip';
import { PhoneFrame, StatusBar } from '../components/PhoneFrame';
import { MicIcon, PhoneIcon, ScriptIcon } from '../components/icons';
import type { CallEndedMsg, PhoneServerMsg } from '../lib/types';
import { useCallTimer } from '../lib/useCallTimer';
import { usePhone } from '../lib/usePhone';

const QUICK_LINES = [
  "Hi, this is Mark from Chase's fraud department, we've seen suspicious charges on your account.",
  'To secure your account, I just need to verify your account number.',
  "Ma'am, don't hang up, and don't tell your family, or your account will be frozen today.",
];

export default function Caller() {
  const [ended, setEnded] = useState<CallEndedMsg | null>(null);
  const [agentLine, setAgentLine] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [dialing, setDialing] = useState(false);
  const [tray, setTray] = useState(false);
  const [sttKey, setSttKey] = useState(0);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const captionsRef = useRef<string[]>([]);
  const gotSubtitle = useRef(false);
  const subs = useSubtitles();

  const enqueueSpeech = (text: string, b64: string | null, mime?: string) => {
    queue.current = queue.current.then(() => sayOut(text, b64, mime)).catch(() => undefined);
  };

  const onMsg = useCallback((m: PhoneServerMsg) => {
    switch (m.type) {
      case 'subtitle':
        gotSubtitle.current = true;
        subs.push(m);
        break;
      case 'caption':
        // Fallback for backends that only send mock-mode captions.
        if (gotSubtitle.current) break;
        captionsRef.current = [...captionsRef.current, m.text].slice(-12);
        subs.push({ speaker: 'granny', utt: -1, text: captionsRef.current.join(' '), final: true });
        break;
      case 'agent_say':
        setAgentLine(m.text);
        enqueueSpeech(m.text, m.audio_b64, m.mime);
        break;
      case 'call_ended':
        setEnded(m);
        setDialing(false);
        setTray(false);
        if (m.message || m.audio_b64) enqueueSpeech(m.message, m.audio_b64 ?? null, m.mime);
        break;
      case 'stt_restart':
        setSttKey((k) => k + 1);
        break;
      case 'state':
        if (m.call === 'ringing' || m.call === 'active') setEnded(null);
        break;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const { connected, replaced, state, send, sock } = usePhone('caller', onMsg);
  const call = state?.call ?? 'idle';
  const active = call === 'active' && !ended;
  const ringing = (call === 'ringing' || dialing) && !active && !ended;
  const idle = !ended && (call === 'idle' || call === 'ended') && !dialing;
  const mode = state?.stt_mode ?? 'mock';
  const audio = useCallAudio(sock, active, muted, state?.audio_frame_ms ?? 40);
  useWebSpeech(sock, active && !muted && (mode === 'browser' || mode === 'hybrid'), sttKey);
  const timer = useCallTimer(active);

  useEffect(() => {
    if (ended) audio.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ended]);
  useEffect(() => {
    if (call === 'active' || call === 'ringing') setDialing(false);
  }, [call]);

  const dial = () => {
    setEnded(null);
    captionsRef.current = [];
    subs.clear();
    setAgentLine(null);
    setMuted(false);
    setDialing(true);
    try {
      window.speechSynthesis?.resume();
    } catch {
      /* ignore */
    }
    audio.start();
    send({ type: 'dial' });
  };
  const hangup = () => {
    send({ type: 'hangup' });
    setDialing(false);
    setTray(false);
    audio.stop();
  };

  const byAgent = ended?.by === 'agent';

  return (
    <div className="page page--caller">
      <div className="phone-col">
      <PhoneFrame variant="dark">
        <StatusBar />

        {ended ? (
          <div className={`ended ${byAgent ? 'ended--agent' : ''}`}>
            <div className="ended__icon">{byAgent ? <ShieldMark /> : <PhoneIcon down />}</div>
            <div className="ended__title">Call ended</div>
            {byAgent && <div className="ended__by">ended by Granny-Protect-5000</div>}
            {ended.message && <p className="ended__msg">“{ended.message}”</p>}
            <button className="btn-pill" onClick={() => setEnded(null)}>
              Done
            </button>
          </div>
        ) : idle ? (
          <div className="call call--idle">
            <div className="call__who">
              <div className="avatar">G</div>
              <div className="call__name">Grandma</div>
              <div className="call__sub">mobile · (555) 014-2290</div>
            </div>
            <div className="call__actions">
              <button className="dial" onClick={dial} disabled={!connected} aria-label="Call Grandma">
                <PhoneIcon />
              </button>
              <div className="call__hint">{connected ? 'Call Grandma' : replaced ? 'Open on another device' : 'connecting…'}</div>
            </div>
          </div>
        ) : (
          <div className="call">
            <div className="call__who">
              <div className={`avatar ${active ? 'avatar--live' : 'avatar--ringing'}`}>G</div>
              <div className="call__name">Grandma</div>
              <div className="call__sub">{ringing ? 'calling…' : timer}</div>
            </div>

            <div className="livecap" aria-live="polite">
              {agentLine && !gotSubtitle.current && <div className="livecap__agent">{agentLine}</div>}
            </div>

            {audio.micError && <div className="hint hint--warn">Mic: {audio.micError}</div>}

            <div className="call__grid">
              <button className={`key ${muted ? 'key--on' : ''}`} onClick={() => setMuted((m) => !m)} disabled={!active}>
                <MicIcon off={muted} />
                <span>{muted ? 'unmute' : 'mute'}</span>
              </button>
              {mode === 'mock' && (
                <button className={`key ${tray ? 'key--on' : ''}`} onClick={() => setTray((t) => !t)} disabled={!active}>
                  <ScriptIcon />
                  <span>script</span>
                </button>
              )}
            </div>
            <button className="hangup" onClick={hangup} aria-label="End call">
              <PhoneIcon down />
            </button>

            <div className={`tray ${tray && active ? 'is-open' : ''}`}>
              <div className="tray__grip" onClick={() => setTray(false)} />
              <MockSay
                variant="dark"
                lines={QUICK_LINES}
                onSay={(text) => {
                  send({ type: 'say', text });
                }}
              />
            </div>
          </div>
        )}
      </PhoneFrame>
      <SubtitleStrip lines={subs.lines} now={subs.now} tone="dark" page="caller" />
      </div>
    </div>
  );
}

function ShieldMark() {
  return (
    <svg viewBox="0 0 24 24" width="56" height="56" aria-hidden>
      <path fill="currentColor" d="M12 2 4 5v6c0 5 3.4 9.7 8 11 4.6-1.3 8-6 8-11V5l-8-3z" />
    </svg>
  );
}
