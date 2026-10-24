import { useEffect, useState } from 'react';
import type { ConfigResp, Heartbeat, ScriptInfo } from '../lib/types';
import { getJson, postJson } from '../lib/ws';

interface Props {
  open: boolean;
  heartbeat: Heartbeat | null;
  onClose: () => void;
  onReset: () => void;
}

export function ControlsDrawer({ open, heartbeat, onClose, onReset }: Props) {
  const [scripts, setScripts] = useState<ScriptInfo[] | null>(null);
  const [challenge, setChallenge] = useState(false);
  const [minutes, setMinutes] = useState(120);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    getJson<ScriptInfo[]>('/api/scripts').then(setScripts);
    getJson<ConfigResp>('/api/config').then((c) => c && setChallenge(!!c.challenge_mode));
  }, [open]);

  const run = async (label: string, fn: () => Promise<boolean>) => {
    setBusy(label);
    const ok = await fn();
    setBusy(null);
    setMsg(ok ? `${label}: done` : `${label}: failed (backend offline?)`);
    window.setTimeout(() => setMsg(null), 2500);
    return ok;
  };

  const squeeze = async () => {
    // Fetch fresh spend so the squeeze lands just above it.
    const hb = (await getJson<Heartbeat>('/api/shift')) ?? heartbeat;
    const spend = hb?.spend_usd ?? 0;
    return run('Squeeze budget', () => postJson('/api/budget', { shift_usd: +(spend + 0.002).toFixed(4) }));
  };

  const ap = heartbeat?.autopilot;

  return (
    <>
      <div className={`drawer-scrim ${open ? 'is-open' : ''}`} onClick={onClose} />
      <aside className={`drawer ${open ? 'is-open' : ''}`} aria-hidden={!open}>
        <div className="drawer__head">
          <h3>Demo controls</h3>
          <button className="btn btn--ghost" onClick={onClose}>
            Close
          </button>
        </div>

        <section>
          <h4>Replay a call</h4>
          {scripts === null && <div className="muted small">Loading… (or backend offline)</div>}
          <div className="script-list">
            {scripts?.map((s) => (
              <button
                key={s.id}
                className="script"
                disabled={!!busy}
                onClick={async () => {
                  if (await run(`Replay: ${s.title}`, () => postJson(`/api/replay/${encodeURIComponent(s.id)}`))) onClose();
                }}
              >
                <span className={`script__kind script__kind--${s.kind}`}>{s.kind}</span>
                <span className="script__title">{s.title}</span>
                <span className="muted num">max {s.expected_max_level}</span>
              </button>
            ))}
          </div>
        </section>

        <section>
          <h4>Unattended shift</h4>
          <div className="drawer__row">
            <label className="minutes">
              <input type="number" min={1} max={1440} value={minutes} onChange={(e) => setMinutes(Math.max(1, +e.target.value || 120))} />
              <span>min</span>
            </label>
            <button className="btn" disabled={!!busy} onClick={() => run('Autopilot started', () => postJson('/api/autopilot', { minutes }))}>
              Start autopilot
            </button>
            <button className="btn btn--ghost" disabled={!!busy} onClick={() => run('Autopilot stopped', () => postJson('/api/autopilot/stop'))}>
              Stop
            </button>
          </div>
          <div className="muted small">{ap?.running ? `Running · ${ap.calls} calls so far` : ap?.stop_reason ? `Stopped: ${ap.stop_reason}` : 'Not running'}</div>
        </section>

        <section>
          <h4>Overspend guard</h4>
          <div className="drawer__row">
            <button className="btn btn--amber" disabled={!!busy} onClick={squeeze} title="Sets the shift budget just above current spend">
              Squeeze budget
            </button>
            <button className="btn btn--ghost" disabled={!!busy} onClick={() => run('Budget reset to $2.00', () => postJson('/api/budget', { shift_usd: 2.0 }))}>
              Reset budget
            </button>
          </div>
          <div className="muted small">
            {heartbeat
              ? `Shift spend $${heartbeat.spend_usd.toFixed(3)} of $${heartbeat.shift_budget_usd.toFixed(3)} · mode ${heartbeat.mode}`
              : 'No heartbeat yet'}
          </div>
        </section>

        <section>
          <h4>Agent</h4>
          <label className="toggle">
            <input
              type="checkbox"
              checked={challenge}
              onChange={async (e) => {
                const v = e.target.checked;
                setChallenge(v);
                if (!(await run('Challenge mode', () => postJson('/api/challenge', { enabled: v })))) setChallenge(!v);
              }}
            />
            <span className="toggle__track" />
            <span>Challenge mode: the agent asks the caller a verification question</span>
          </label>
        </section>

        <section>
          <h4>Session</h4>
          <button
            className="btn btn-danger"
            disabled={!!busy}
            onClick={async () => {
              if (await run('Reset', () => postJson('/api/reset'))) onReset();
            }}
          >
            Reset to idle call
          </button>
        </section>
        {msg && <div className="drawer__msg">{msg}</div>}
        <div className="muted small">
          Press <kbd>H</kbd> on the panel to show what granny experiences.
        </div>
      </aside>
    </>
  );
}
