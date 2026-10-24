import { useEffect, useState } from 'react';
import { Brand } from '../components/Brand';
import type { Report as ReportT } from '../lib/types';
import { LEVEL_NAMES } from '../lib/types';
import { getJson, navigate } from '../lib/ws';

function epochMs(ts: number | string | null | undefined) {
  if (ts == null || ts === '') return NaN;
  return typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts);
}
function fmtDate(ts: number | string | null | undefined) {
  const n = epochMs(ts);
  return isNaN(n) ? '–' : new Date(n).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}
function fmtTime(ts: number | string) {
  const n = epochMs(ts);
  return isNaN(n) ? String(ts) : new Date(n).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function sentence(s: string) {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

export default function Report({ id }: { id: string }) {
  const [rep, setRep] = useState<ReportT | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    let tries = 0;
    const load = async () => {
      const r = await getJson<ReportT>(`/api/calls/${encodeURIComponent(id)}/report`);
      if (!alive) return;
      if (r || tries++ > 3) setRep(r);
      if (!r && tries <= 4) window.setTimeout(load, 1200);
      else if (r?.live) window.setTimeout(load, 3000); // live call: keep the loved one's view current
    };
    load();
    return () => {
      alive = false;
    };
  }, [id]);

  if (rep === undefined)
    return (
      <div className="page page--report">
        <p className="report__loading">Loading report…</p>
      </div>
    );
  if (rep === null)
    return (
      <div className="page page--report">
        <article className="report">
          <h1>Report not available</h1>
          <p className="muted">
            Couldn’t load the report for call <span className="mono">{id}</span>. The backend may be offline.
          </p>
          <button className="btn" onClick={() => navigate('/panel')}>
            Back to panel
          </button>
        </article>
      </div>
    );

  const outcome = sentence(rep.outcome || (rep.ended ? 'Call completed' : 'Call in progress'));
  const blocked = rep.max_level >= 3 || /terminat|blocked|hung up by/i.test(rep.outcome || '');
  const tone = blocked ? 'critical' : rep.max_level >= 1 ? 'warn' : 'ok';
  const peaks = Object.entries(rep.signals_peak || {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="page page--report">
      <article className="report">
        <div className="report__bar">
          <Brand sub="Incident report" />
          <span className="report__actions-bar no-print">
            <button className="btn btn--ghost" onClick={() => navigate('/panel')}>
              Back
            </button>
            <button className="btn" onClick={() => window.print()}>
              Print
            </button>
          </span>
        </div>

        <header className="report__head">
          <div className={`report__status ${tone}`}>
            {rep.live && <span className="report__live">● Live</span>}
            Level {rep.max_level} · {LEVEL_NAMES[rep.max_level]}
          </div>
          <h1>{outcome}</h1>
          <p className="report__meta num">
            {fmtDate(rep.started)} · call {rep.call_id}
            {rep.claimed_org ? ` · caller claimed to be ${rep.claimed_org}` : ''}
            {rep.cost_usd != null ? ` · AI cost $${rep.cost_usd.toFixed(4)}` : ''}
          </p>
        </header>

        {rep.story && rep.story.length > 0 && (
          <section className="story">
            <h2>What happened</h2>
            <ol className="story__list">
              {rep.story.map((s, i) => (
                <li key={i}>
                  <span className="num muted">{fmtTime(s.ts)}</span>
                  <span>{s.text}</span>
                </li>
              ))}
            </ol>
            {rep.live && <p className="muted">This page updates by itself while the call is going on.</p>}
          </section>
        )}

        {rep.safe_next_step && (
          <section className="next">
            <h2>What to do now</h2>
            <p>{rep.safe_next_step}</p>
          </section>
        )}

        <section>
          <h2>Summary</h2>
          <p className="report__summary">{rep.summary}</p>
          {rep.recommendations?.length > 0 && (
            <ul className="report__list">
              {rep.recommendations.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
        </section>

        {(rep.timeline?.length > 0 || peaks.length > 0 || rep.actions?.length > 0) && (
        <div className="report__cols">
          {rep.timeline?.length > 0 && (
          <section>
            <h2>Timeline</h2>
            <ol className="timeline">
              {rep.timeline?.map((t, i) => (
                <li key={i}>
                  <span className="num muted">{fmtTime(t.ts)}</span>
                  <span>{t.text}</span>
                </li>
              ))}
            </ol>
          </section>
          )}
          {(peaks.length > 0 || rep.actions?.length > 0) && (
          <section>
            {peaks.length > 0 && <h2>Peak signals</h2>}
            <div className="peaks">
              {peaks.map(([k, p]) => (
                <div key={k} className="peak">
                  <span>{sentence(k.replace(/_/g, ' '))}</span>
                  <span className="peak__track">
                    <span style={{ width: `${Math.round(p * 100)}%` }} />
                  </span>
                  <span className="num">{Math.round(p * 100)}%</span>
                </div>
              ))}
            </div>

            {rep.actions?.length > 0 && <h2>Actions taken</h2>}
            <ul className="report__plain">
              {rep.actions?.map((a, i) => (
                <li key={i}>
                  <span className="num muted">{fmtTime(a.ts)}</span>
                  <span>
                    {sentence(a.actor)}: {a.tool.replace(/_/g, ' ')}
                    {typeof a.args?.text === 'string' ? ` “${a.args.text}”` : ''}
                    {a.ok ? '' : ' (failed)'}
                  </span>
                </li>
              ))}
            </ul>
          </section>
          )}
        </div>
        )}

        {rep.verification && (
          <section>
            <h2>Identity check</h2>
            <dl className="report__check">
              {rep.verification.question && (
                <>
                  <dt>Question</dt>
                  <dd>“{rep.verification.question}”</dd>
                </>
              )}
              <dt>Answer</dt>
              <dd>
                {rep.verification.answer ? `“${rep.verification.answer}”` : 'No answer'}
                {rep.verification.match != null ? <span className="muted num"> · match {Math.round(rep.verification.match * 100)}%</span> : null}
              </dd>
              <dt>Result</dt>
              <dd className={rep.verification.result === 'passed' ? 'ok' : rep.verification.result === 'failed' ? 'critical' : 'warn'}>
                {rep.verification.result === 'passed' ? 'Verified' : rep.verification.result === 'failed' ? 'Failed, treated as a scam' : sentence(String(rep.verification.result))}
                {rep.verification.reason ? <span className="muted"> · {rep.verification.reason}</span> : null}
              </dd>
            </dl>
          </section>
        )}

        {rep.memory_matches && rep.memory_matches.length > 0 && (
          <section>
            <h2>Recognized from earlier calls</h2>
            <ul className="report__plain">
              {rep.memory_matches.map((m, i) => (
                <li key={i}>
                  <span className="num muted">{Math.round(m.similarity * 100)}%</span>
                  <span>
                    Matches call{' '}
                    <a href={`/report/${encodeURIComponent(m.past_call_id)}`} onClick={(e) => (e.preventDefault(), navigate(`/report/${encodeURIComponent(m.past_call_id)}`))}>
                      {m.past_call_id}
                    </a>
                    {m.past_when_ts != null ? ` from ${fmtDate(m.past_when_ts)}` : ''}
                    {m.excerpt ? `: “${m.excerpt}”` : ''}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {rep.supervisor && rep.supervisor.length > 0 && (
          <section>
            <h2>Supervisor</h2>
            <ul className="report__plain">
              {rep.supervisor.map((s, i) => (
                <li key={i} className={s.severity === 'info' ? '' : s.severity}>
                  <span className="num muted">{fmtTime(s.ts)}</span>
                  <span>{s.message}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {rep.excerpts?.length > 0 && (
          <section>
            <h2>Transcript excerpts</h2>
            <div className="excerpts">
              {rep.excerpts.map((e, i) => (
                <p key={i} className={`excerpt excerpt--${e.speaker}`}>
                  <span className="muted">{e.speaker === 'caller' ? 'Caller' : 'Granny'}</span> “{e.text}”
                </p>
              ))}
            </div>
          </section>
        )}
      </article>
    </div>
  );
}
