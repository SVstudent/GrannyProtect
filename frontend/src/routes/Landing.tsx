import type { MouseEvent } from 'react';
import { Brand } from '../components/Brand';
import { useConfig } from '../components/ProviderBadges';
import { navigate } from '../lib/ws';

const CARDS = [
  { href: '/granny', tone: 'warm', kicker: 'Victim phone', title: 'Granny', sub: 'Calm, warm, no app. Just a voice that has her back.' },
  { href: '/caller', tone: 'red', kicker: 'Scammer phone', title: 'Caller', sub: 'Dial in and try to talk her out of her account number.' },
  { href: '/panel', tone: 'violet', kicker: 'Agent mind', title: 'Panel', sub: 'Watch it listen, decide, act, and supervise itself.' },
];

function go(e: MouseEvent, href: string) {
  if (e.metaKey || e.ctrlKey || e.shiftKey) return;
  e.preventDefault();
  navigate(href);
}

export default function Landing() {
  const cfg = useConfig();
  const models = cfg?.models ? Object.entries(cfg.models).filter(([, v]) => v) : [];
  return (
    <div className="page page--landing">
      <div className="landing">
        <Brand />
        <h1 className="landing__pitch">
          An invisible guardian on every call.
          <span> It listens, whispers, cuts the mic, hangs up, and supervises itself.</span>
        </h1>

        <div className="landing__cards">
          {CARDS.map((c) => (
            <a key={c.href} href={c.href} className={`lcard lcard--${c.tone}`} onClick={(e) => go(e, c.href)}>
              <span className="lcard__kicker">{c.kicker}</span>
              <span className="lcard__title">{c.title}</span>
              <span className="lcard__sub">{c.sub}</span>
              <span className="lcard__arrow">→</span>
            </a>
          ))}
        </div>

        <div className="landing__foot">
          <a href="/stage" onClick={(e) => go(e, '/stage')} className="landing__stage">
            Stage view for the projector →
          </a>
          <span className="landing__cfg">
            {cfg === null && 'backend offline'}
            {cfg &&
              (models.length
                ? models.map(([k, v]) => (
                    <span key={k} className="mtag" title={k}>
                      <b>{k}</b> {String(v)}
                    </span>
                  ))
                : `STT ${cfg.stt_provider} · signals ${cfg.signals_provider} · agent ${cfg.agent_provider} · TTS ${cfg.tts_provider}`)}
          </span>
        </div>
      </div>
    </div>
  );
}
