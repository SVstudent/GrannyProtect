import { useState } from 'react';
import type { PanelState } from '../lib/panelState';
import { pickSignals } from '../lib/panelState';
import { SIGNAL_NAMES } from '../lib/types';

const LABEL: Record<string, string> = {
  claims_authority: 'Authority',
  requests_credentials: 'Credentials',
  requests_unusual_payment: 'Payment',
  creates_urgency: 'Urgency',
  demands_secrecy: 'Secrecy',
  threatens: 'Threats',
};

/** Scam score + claimed org + top two signals. Full bars sit behind a disclosure. */
export function SignalBars({ state }: { state: PanelState }) {
  const [open, setOpen] = useState(false);
  const { shown, regex, isFastPath } = pickSignals(state);
  const score = shown?.scam_score ?? 0;
  const ranked = SIGNAL_NAMES.map((n) => ({ n, p: shown?.signals?.[n]?.p ?? 0 })).sort((a, b) => b.p - a.p);
  const top = ranked.filter((r) => r.p >= 0.5).slice(0, 2);
  const tone = score >= 0.75 ? 'critical' : score >= 0.5 ? 'warn' : '';

  return (
    <div className="sig">
      <div className="label">Scam score</div>
      <div className={`sig__score num ${tone}`}>{shown ? `${Math.round(score * 100)}%` : '–'}</div>
      {state.claimedOrg && (
        <div className="sig__org">
          Claims to be <b>{state.claimedOrg}</b>
        </div>
      )}
      <div className="sig__top">
        {top.length ? top.map((t) => `${LABEL[t.n]} ${Math.round(t.p * 100)}%`).join(' · ') : shown ? 'No strong signals' : 'Listening'}
      </div>
      {shown && (
        <button className="disclose" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? 'Hide signals' : 'All signals'}
        </button>
      )}
      {open && shown && (
        <div className="sig__all">
          {SIGNAL_NAMES.map((n) => {
            const p = Math.max(0, Math.min(1, shown.signals?.[n]?.p ?? 0));
            return (
              <div key={n} className="sig__row">
                <span>{LABEL[n]}</span>
                <span className="sig__track">
                  <span className={`sig__fill ${p >= 0.75 ? 'critical' : p >= 0.5 ? 'warn' : ''}`} style={{ width: `${p * 100}%` }} />
                </span>
                <span className="num">{Math.round(p * 100)}%</span>
              </div>
            );
          })}
          <div className="sig__prov num">
            {shown.provider} · {Math.round(shown.latency_ms)} ms
            {regex && (isFastPath ? ' (fast path)' : ` · fast path regex ${Math.round(regex.latency_ms)} ms`)}
          </div>
          {shown.rationale && <p className="sig__why">{shown.rationale}</p>}
        </div>
      )}
    </div>
  );
}
