import type { ReactNode } from 'react';
import type { PanelState, Utterance } from '../lib/panelState';
import { useAutoScroll } from './useAutoScroll';

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function highlight(text: string, hits: string[]): ReactNode {
  const words = hits.map((h) => h.trim()).filter(Boolean);
  if (!words.length) return text;
  const re = new RegExp(`(${words.sort((a, b) => b.length - a.length).map(escapeRe).join('|')})`, 'gi');
  return text.split(re).map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : p));
}

export function LockGlyph() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden>
      <path fill="currentColor" d="M4.5 7V5a3.5 3.5 0 0 1 7 0v2h.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h.5zm1.5 0h4V5a2 2 0 0 0-4 0v2z" />
    </svg>
  );
}

type Row =
  | { kind: 'utt'; seq: number; u: Utterance }
  | { kind: 'private'; seq: number; p: PanelState['privates'][number] }
  | { kind: 'guardian'; seq: number; text: string };

export function Transcript({ state }: { state: PanelState }) {
  // A private question to the guardian is shown once, inside the private block.
  const norm = (t: string) => t.toLowerCase().replace(/[^a-z0-9 ]/g, '').trim();
  const asked = new Set(state.privates.map((p) => norm(p.question)));
  const rows: Row[] = [
    ...state.transcript.filter((u) => !(u.speaker === 'granny' && asked.has(norm(u.text)))).map((u) => ({ kind: 'utt' as const, seq: u.seq, u })),
    ...state.privates.map((p) => ({ kind: 'private' as const, seq: p.seq, p })),
    ...state.guardianSaid.map((g) => ({ kind: 'guardian' as const, seq: g.seq, text: g.text })),
  ].sort((a, b) => a.seq - b.seq);
  const ref = useAutoScroll<HTMLDivElement>(rows.length + (rows[rows.length - 1]?.kind === 'utt' ? (rows[rows.length - 1] as { u: Utterance }).u.text.length : 0));

  return (
    <div className="transcript" ref={ref}>
      {!rows.length && <p className="empty">The conversation appears here.</p>}
      {rows.map((r) =>
        r.kind === 'utt' ? (
          <div key={r.u.key} className={`msg msg--${r.u.speaker} ${r.u.partial ? 'msg--partial' : ''}`}>
            <div className="msg__who">
              {r.u.speaker === 'caller' ? 'Caller' : 'Granny'}
              {r.u.refined && <span className="msg__tag">TokenRouter STT ✓</span>}
            </div>
            <div className="msg__text">{highlight(r.u.text, r.u.hits)}</div>
          </div>
        ) : r.kind === 'guardian' ? (
          <div key={`g${r.seq}`} className="gsaid">
            <div className="gsaid__label">Guardian (on the call)</div>
            <div className="gsaid__text">“{r.text}”</div>
          </div>
        ) : (
          <div key={`p${r.seq}`} className="private">
            <div className="private__label">
              <LockGlyph /> Private to granny · caller can’t hear
            </div>
            <div className="private__q">Granny: “{r.p.question}”</div>
            <div className="private__a">Guardian: “{r.p.answer}”</div>
          </div>
        ),
      )}
    </div>
  );
}
