import { useEffect, useRef, useState } from 'react';
import type { Heartbeat, Severity } from '../lib/types';

export interface SupFlash {
  seq: number;
  ts: number | string;
  kind: string;
  severity: Severity;
  message: string;
}

const FLASH_MS = 6000;
const PROVIDER_NAME: Record<string, string> = { jev: 'Signals', agent: 'Agent', stt: 'Speech-to-text', tts: 'Voice' };

export function hms(sec: number) {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
function hm(sec: number) {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`;
}
// Money: show enough digits that sub-cent agent spend never reads as $0.00.
const usd = (n: number | null | undefined, d = 2) => {
  const v = n ?? 0;
  if (v > 0 && v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(d)}`;
};

/** Re-render every second so clocks move between 2 s heartbeats. */
function useDrift(hb: Heartbeat | null) {
  const [since, setSince] = useState(() => Date.now());
  const [, force] = useState(0);
  useEffect(() => setSince(Date.now()), [hb]);
  useEffect(() => {
    const id = window.setInterval(() => force((x) => x + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (Date.now() - since) / 1000;
}

/** Quiet by default. Briefly shows a message on a mode change or a warn/critical supervisor event. */
function useFlash(hb: Heartbeat | null, last: SupFlash | null) {
  const [flash, setFlash] = useState<{ severity: Severity; message: string } | null>(null);
  const prevMode = useRef<string | null>(null);
  const prevSeq = useRef<number | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const fire = (f: { severity: Severity; message: string }) => {
    setFlash(f);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setFlash(null), FLASH_MS);
  };
  useEffect(() => {
    if (!last || last.seq === prevSeq.current) return;
    prevSeq.current = last.seq;
    const t = typeof last.ts === 'number' ? (last.ts > 1e12 ? last.ts / 1000 : last.ts) : Date.parse(last.ts) / 1000;
    const fresh = !isFinite(t) || Date.now() / 1000 - t < 10; // skip events replayed on connect
    if (fresh && (last.severity !== 'info' || last.kind === 'mode')) fire({ severity: last.severity, message: last.message });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [last?.seq]);
  useEffect(() => {
    if (!hb) return;
    const was = prevMode.current;
    prevMode.current = hb.mode;
    if (was && was !== hb.mode) {
      fire({
        severity: hb.mode === 'full' ? 'info' : hb.mode === 'economy' ? 'warn' : 'critical',
        message: `Switched to ${hb.mode.replace('-', ' ')} mode${hb.mode_reason ? `: ${hb.mode_reason}` : ''}`,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hb?.mode]);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return flash;
}

/** One quiet line: the guardian's on-duty status. Details behind a disclosure. */
export function SupervisorStrip({ hb, last = null }: { hb: Heartbeat | null; last?: SupFlash | null }) {
  const drift = useDrift(hb);
  const flash = useFlash(hb, last);
  const [open, setOpen] = useState(false);

  if (!hb) return <div className="duty muted">On duty</div>;

  const problems = Object.entries(hb.providers || {}).filter(([, p]) => p.state !== 'ok');
  const ap = hb.autopilot;
  const apElapsed = ap?.running && ap.started ? Date.now() / 1000 - ap.started : 0;

  return (
    <div className={`duty ${flash ? `duty--flash ${flash.severity}` : ''}`}>
      <button className="duty__line" onClick={() => setOpen((o) => !o)} aria-expanded={open} title="Supervisor details">
        {flash ? (
          <span className="duty__msg">{flash.message}</span>
        ) : (
          <>
            <span>
              On duty <span className="num">{hms(hb.uptime_s + drift)}</span>
            </span>
            <span className="num">{hb.calls} calls</span>
            <span className="num">{hb.scams_stopped} stopped</span>
            <span className="num">{usd(hb.spend_usd)}</span>
            {hb.mode !== 'full' && <span className={hb.mode === 'economy' ? 'warn' : 'critical'}>{hb.mode === 'economy' ? 'Economy mode' : 'Rules-only mode'}</span>}
            {problems.map(([k, p]) => (
              <span key={k} className={p.state === 'open' ? 'critical' : 'warn'}>
                {PROVIDER_NAME[k] ?? k} {p.state === 'open' ? 'down' : 'slow'}
              </span>
            ))}
            {ap?.running && (
              <span className="accent num">
                Autopilot {hm(apElapsed)} / {hm((ap.minutes ?? 0) * 60)} · {ap.calls} calls
                {ap.accuracy != null ? ` · ${Math.round(ap.accuracy * 100)}%` : ''}
              </span>
            )}
          </>
        )}
      </button>

      {open && (
        <div className="duty__panel">
          <dl>
            <dt>Mode</dt>
            <dd>
              {hb.mode.replace('-', ' ')}
              {hb.mode_reason ? ` · ${hb.mode_reason}` : ''}
            </dd>
            <dt>This call</dt>
            <dd className="num">
              {usd(hb.call_spend_usd, 4)} of {usd(hb.call_budget_usd, 2)}
            </dd>
            <dt>Shift</dt>
            <dd className="num">
              {usd(hb.spend_usd)} of {usd(hb.shift_budget_usd, 2)} · {usd(hb.spend_per_hour)}/hr
            </dd>
            <dt>Caught</dt>
            <dd className="num">
              {hb.scams_stopped} scams · {hb.pii_blocked} PII · {hb.loops_caught} loops · {hb.recoveries} recoveries
            </dd>
            <dt>Providers</dt>
            <dd className="num">
              {Object.entries(hb.providers || {})
                .map(([k, p]) => `${PROVIDER_NAME[k] ?? k} ${p.state}${p.p50_ms != null ? ` ${Math.round(p.p50_ms)} ms` : ''}`)
                .join(' · ')}
            </dd>
            <dt>Autopilot</dt>
            <dd>{ap?.running ? `running, ${ap.calls} calls` : ap?.stop_reason ? `stopped: ${ap.stop_reason}` : 'off'}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}

/** Calm idle line with shift stats. */
export function IdleDuty({ hb }: { hb: Heartbeat | null }) {
  const drift = useDrift(hb);
  return (
    <div className="idle">
      <p className="idle__title">On duty · waiting for a call</p>
      {hb && (
        <p className="idle__stats num">
          {hb.calls} calls · {hb.scams_stopped} scams stopped · {hb.pii_blocked} PII blocked · {hb.loops_caught} loops caught · up {hms(hb.uptime_s + drift)}
        </p>
      )}
    </div>
  );
}
