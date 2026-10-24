import { useEffect, useMemo, useState } from 'react';
import type { PanelState } from '../lib/panelState';
import { navigate } from '../lib/ws';
import { Brand } from './Brand';
import { ControlsDrawer } from './ControlsDrawer';
import { DecisionFeed } from './DecisionFeed';
import { LevelLadder } from './LevelLadder';
import { SignalBars } from './SignalBars';
import { IdleDuty, SupervisorStrip, type SupFlash } from './SupervisorStrip';
import { Transcript } from './Transcript';

function VerifyStatus({ stage }: { stage: PanelState['verifyStage'] }) {
  if (!stage) return null;
  if (stage === 'passed') return <span className="vstatus ok">Verified</span>;
  if (stage === 'failed') return <span className="vstatus critical">Failed check</span>;
  if (stage === 'timeout') return <span className="vstatus warn">No answer to check</span>;
  return <span className="vstatus accent">Verifying identity…</span>;
}

interface Props {
  state: PanelState;
  connected: boolean;
  clear: () => void;
  embedded?: boolean;
}

function callLabel(s: PanelState) {
  switch (s.callState) {
    case 'active':
      return 'Live call';
    case 'ringing':
      return 'Ringing';
    case 'ended':
      return s.endedBy === 'agent' ? 'Ended by the guardian' : s.endedBy ? `Ended by ${s.endedBy}` : 'Call ended';
    default:
      return 'No call';
  }
}

/**
 * The agent-mind panel. Reads in story order:
 * status → threat level → conversation → decisions.
 */
export function PanelView({ state, connected, clear, embedded }: Props) {
  const [calm, setCalm] = useState(false);
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === 'h' || e.key === 'H') setCalm((c) => !c);
      if (e.key === 'Escape') setDrawer(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const lastSup = useMemo<SupFlash | null>(() => {
    for (let i = state.feed.length - 1; i >= 0; i--) {
      const e = state.feed[i];
      if (e.type === 'supervisor') return { seq: e.seq, ts: e.ts, ...e.data };
    }
    return null;
  }, [state.feed]);

  const idle =
    (state.callState === 'idle' || state.callState === 'unknown') && !state.transcript.length && !state.feed.some((e) => e.type !== 'supervisor' && e.type !== 'info');

  return (
    <div className={`panel ${embedded ? 'panel--embedded' : ''}`}>
      <header className="bar">
        <Brand />
        <span className={`bar__call ${state.callState === 'active' ? 'is-live' : ''}`}>
          {state.callState === 'active' && <i className="live-dot" />}
          {callLabel(state)}
        </span>
        {state.reportCallId && (
          <a
            className="bar__report"
            href={`/report/${encodeURIComponent(state.reportCallId)}`}
            onClick={(e) => {
              e.preventDefault();
              navigate(`/report/${encodeURIComponent(state.reportCallId!)}`);
            }}
          >
            Incident report →
          </a>
        )}
        {!connected && <span className="critical small">Reconnecting…</span>}
        <span className="bar__spacer" />
        <SupervisorStrip hb={state.heartbeat} last={lastSup} />
        <button className="bar__menu" onClick={() => setDrawer(true)} aria-label="Demo controls">
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden>
            <path fill="currentColor" d="M4 7h16v2H4zm0 4h16v2H4zm0 4h16v2H4z" />
          </svg>
        </button>
      </header>

      {idle ? (
        <IdleDuty hb={state.heartbeat} />
      ) : (
        <>
          <section className="threat">
            <div className="threat__level">
              <div className="label">
                Threat level
                <VerifyStatus stage={state.verifyStage} />
              </div>
              <LevelLadder level={state.level} animKey={state.levelSeq} />
            </div>
            <SignalBars state={state} />
            <div className="hears">
              <div className="label">
                Granny hears
                <span className={`hears__mic ${state.micCut ? 'is-on' : ''}`}>Mic cut</span>
              </div>
              {state.lastWhisper ? (
                <p key={state.lastWhisper.seq} className="hears__text">
                  “{state.lastWhisper.text}”
                </p>
              ) : (
                <p className="hears__none">Nothing yet. The guardian stays quiet until it’s needed.</p>
              )}
            </div>
          </section>

          <div className="story">
            <section className="story__col">
              <h2 className="label">Conversation</h2>
              <Transcript state={state} />
            </section>
            <section className="story__col">
              <h2 className="label">Decisions</h2>
              <DecisionFeed state={state} />
            </section>
          </div>
        </>
      )}

      <ControlsDrawer open={drawer} heartbeat={state.heartbeat} onClose={() => setDrawer(false)} onReset={clear} />

      <div className={`calm ${calm ? 'is-on' : ''}`} onClick={() => setCalm(false)}>
        <div className="calm__orb" />
        <p className="calm__text">
          This is all granny experiences.
          <br />
          <span>No screen, just a voice that has her back.</span>
        </p>
        <div className="calm__hint">Press H to return</div>
      </div>
    </div>
  );
}
