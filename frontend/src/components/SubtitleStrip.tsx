import { Fragment, useCallback, useEffect, useRef, useState } from 'react';
import type { SubtitleMsg } from '../lib/types';

const MAX_LINES = 3;
const LIFE_MS = 8000;
const FADE_MS = 600;
const CC_KEY = 'gp5000.cc';

export interface SubLine {
  key: string;
  speaker: SubtitleMsg['speaker'];
  text: string;
  final: boolean;
  private: boolean;
  redacted: boolean;
  at: number; // last update, ms
}

/** Partials replace the line for speaker+utt; finals commit it. Lines expire ~8 s after their last update. */
export function useSubtitles() {
  const [lines, setLines] = useState<SubLine[]>([]);
  const [now, setNow] = useState(() => Date.now());

  const push = useCallback((m: Pick<SubtitleMsg, 'speaker' | 'text' | 'final' | 'utt' | 'private' | 'redacted'>) => {
    if (!m.text?.trim()) return;
    const key = `${m.speaker}:${m.utt}:${m.private ? 'p' : ''}`;
    setLines((prev) => {
      const line: SubLine = { key, speaker: m.speaker, text: m.text, final: m.final, private: !!m.private, redacted: !!m.redacted, at: Date.now() };
      const i = prev.findIndex((l) => l.key === key);
      if (i === -1) return [...prev, line].slice(-MAX_LINES * 2);
      if (prev[i].final && !m.final) return prev; // a late partial never overwrites a final
      const next = prev.slice();
      next[i] = line;
      return next;
    });
  }, []);

  const clear = useCallback(() => setLines([]), []);

  // Tick for fade-out; drop expired lines.
  useEffect(() => {
    const id = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      setLines((prev) => (prev.some((l) => t - l.at > LIFE_MS) ? prev.filter((l) => t - l.at <= LIFE_MS) : prev));
    }, 250);
    return () => window.clearInterval(id);
  }, []);

  return { lines: lines.slice(-MAX_LINES), now, push, clear };
}

function readCC(): boolean {
  try {
    return localStorage.getItem(CC_KEY) !== 'off';
  } catch {
    return true;
  }
}
function writeCC(on: boolean) {
  try {
    localStorage.setItem(CC_KEY, on ? 'on' : 'off');
  } catch {
    /* storage unavailable */
  }
}

function whoLabel(l: SubLine, page: 'caller' | 'granny') {
  if (l.speaker === 'caller') return 'Caller';
  if (l.speaker === 'granny') return l.private ? 'Granny · private' : 'Granny';
  // Guardian: private = a whisper to granny; otherwise said out loud on the call.
  if (l.private) return 'Guardian · private';
  return page === 'granny' ? 'Guardian · on the call' : 'Guardian';
}

/** Masked digits ("••••") render as a quiet "hidden" chip. */
function renderText(l: SubLine) {
  if (!/•/.test(l.text)) return l.text;
  // One chip per masked run, even when groups are separated by spaces ("•••• •••• ••••").
  return l.text.split(/(•[•\s]*•|•)/).map((part, i) =>
    /^•[•\s]*$/.test(part) ? (
      <span key={i} className="masked" title="Digits hidden by the guardian">
        •••• hidden
      </span>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}

/** Live subtitles under a phone frame, with a CC toggle remembered in localStorage. */
export function SubtitleStrip({ lines, now, tone, page }: { lines: SubLine[]; now: number; tone: 'dark' | 'warm'; page: 'caller' | 'granny' }) {
  const [on, setOn] = useState(readCC);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className={`subs subs--${tone} ${on ? '' : 'is-off'}`}>
      {on && (
        <div className="subs__list" ref={listRef} aria-live="polite">
          {lines.map((l) => {
            const age = now - l.at;
            const who = whoLabel(l, page);
            return (
              <div
                key={l.key}
                className={`subs__line subs__line--${l.speaker} ${l.private ? 'is-private' : ''} ${l.final ? '' : 'is-partial'} ${age > LIFE_MS - FADE_MS ? 'is-fading' : ''}`}
              >
                <span className="subs__who">{who}</span>
                <span className="subs__text">{renderText(l)}</span>
              </div>
            );
          })}
        </div>
      )}
      <button
        className={`cc ${on ? 'is-on' : ''}`}
        onClick={() => {
          setOn((v) => {
            writeCC(!v);
            return !v;
          });
        }}
        aria-pressed={on}
        title={on ? 'Hide subtitles' : 'Show subtitles'}
      >
        CC
      </button>
    </div>
  );
}
