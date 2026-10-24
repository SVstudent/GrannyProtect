import { useEffect, useState } from 'react';
import type { ConfigResp } from '../lib/types';
import { getJson } from '../lib/ws';

export function useConfig() {
  const [cfg, setCfg] = useState<ConfigResp | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    const load = () => getJson<ConfigResp>('/api/config').then((c) => alive && setCfg(c));
    load();
    const id = window.setInterval(load, 15000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);
  return cfg;
}

/** Provider + model badges from GET /api/config. */
export function ProviderBadges({ cfg, showModels = true }: { cfg: ConfigResp | null | undefined; showModels?: boolean }) {
  if (cfg === undefined) return <span className="badge badge--muted">loading config…</span>;
  if (cfg === null) return <span className="badge badge--bad">backend offline</span>;
  const rows: [string, string | undefined, string | undefined][] = [
    ['STT', cfg.stt_provider, cfg.models?.stt],
    ['Signals', cfg.signals_provider, cfg.models?.signals],
    ['Agent', cfg.agent_provider, cfg.models?.agent],
    ['TTS', cfg.tts_provider, cfg.models?.tts],
  ];
  return (
    <span className="badges">
      {rows.map(([k, p, m]) => (
        <span key={k} className="badge" title={m ? `${p} · ${m}` : p}>
          <b>{k}</b> {p}
          {showModels && m && <span className="badge__model">{m}</span>}
        </span>
      ))}
      {cfg.challenge_mode && <span className="badge badge--accent">challenge</span>}
    </span>
  );
}
