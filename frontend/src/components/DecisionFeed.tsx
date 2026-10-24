import { useState, type ReactNode } from 'react';
import { relTime, type FeedEvent, type PanelState } from '../lib/panelState';
import { LEVEL_NAMES, type PanelEventOf } from '../lib/types';
import { useAutoScroll } from './useAutoScroll';

// ---------- Grouping: a rule + the actions it caused reads as one beat ----------
type RuleEv = PanelEventOf<'rule_fired'>;
type VerifyEv = PanelEventOf<'verification'>;
type Item =
  | { kind: 'group'; key: string; head: RuleEv; children: FeedEvent[]; repeat: number }
  | { kind: 'verify'; key: string; events: VerifyEv[] }
  | { kind: 'single'; key: string; ev: FeedEvent };

const VERDICTS = new Set(['passed', 'failed', 'timeout']);

const CHILD_TYPES = new Set(['tool_call', 'blocked_by_policy', 'pii_guard', 'agent_thought']);

function buildItems(feed: FeedEvent[]): Item[] {
  const items: Item[] = [];
  let cur: Extract<Item, { kind: 'group' }> | null = null;
  let verify: Extract<Item, { kind: 'verify' }> | null = null;
  for (const ev of feed) {
    if (ev.type === 'verification') {
      // One card per identity check, however its stages interleave with other events.
      const done = verify && verify.events.some((e) => VERDICTS.has(e.data.stage));
      if (!verify || (ev.data.stage === 'started' && done)) {
        verify = { kind: 'verify', key: `v${ev.seq}`, events: [] };
        items.push(verify);
      }
      verify.events.push(ev);
      cur = null; // later actions read after the check, not under an earlier rule
      continue;
    }
    if (ev.type === 'rule_fired') {
      const prev = items[items.length - 1];
      // Collapse back-to-back repeats of the same quiet rule ("no warning signs" ×3).
      if (prev && prev.kind === 'group' && prev.head.data.rule_id === ev.data.rule_id && !prev.children.length && ev.data.level === ev.data.prev_level) {
        prev.repeat++;
        cur = prev;
        continue;
      }
      cur = { kind: 'group', key: `g${ev.seq}`, head: ev, children: [], repeat: 1 };
      items.push(cur);
    } else if (CHILD_TYPES.has(ev.type)) {
      if (cur) cur.children.push(ev);
      else items.push({ kind: 'single', key: `s${ev.seq}`, ev });
    } else if (ev.type === 'level_change' || ev.type === 'tool_result' || ev.type === 'info') {
      // level_change is shown on the rule line; results merge into their call; info is noise.
      if (ev.type === 'level_change' && !cur) items.push({ kind: 'single', key: `s${ev.seq}`, ev });
    } else {
      items.push({ kind: 'single', key: `s${ev.seq}`, ev });
    }
  }
  return items;
}

// ---------- Plain English ----------
function quote(v: unknown) {
  return typeof v === 'string' && v ? `“${v}”` : '';
}

const TOOL_VERB: Record<string, string> = {
  whisper_to_granny: 'Whispered to granny',
  terminate_call: 'Hung up on the caller',
  vibrate_granny: "Buzzed granny's phone",
  notify_family: 'Notified the family',
  write_incident_report: 'Wrote the incident report',
  challenge_caller: 'Asked the caller',
  log_event: 'Logged the event',
  log: 'Logged the event',
  mute_granny: "Cut granny's mic",
};
const TOOL_TRY: Record<string, string> = {
  terminate_call: 'hang up',
  challenge_caller: 'question the caller',
  whisper_to_granny: 'whisper to granny',
  notify_family: 'notify the family',
};

function toolLine(tool: string, args: Record<string, unknown> | undefined) {
  const verb = TOOL_VERB[tool] ?? tool.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
  const q = quote(args?.text ?? args?.question ?? args?.message);
  return q ? `${verb} ${q}` : verb;
}

function json(v: unknown) {
  try {
    return JSON.stringify(v, null, 1);
  } catch {
    return String(v);
  }
}

const SUP_GLYPH: Record<string, string> = {
  budget: '$',
  loop: '↻',
  stuck: '‖',
  recovered: '✓',
  mode: '⇄',
  stop: '■',
  autopilot: '▶︎',
};

// ---------- Rendering ----------
interface LineProps {
  time: string;
  actor: string;
  tone?: 'warn' | 'critical' | 'quiet' | 'accent';
  child?: boolean;
  glyph?: string;
  details?: ReactNode;
  children: ReactNode;
}

function Line({ time, actor, tone, child, glyph, details, children }: LineProps) {
  const [open, setOpen] = useState(false);
  const expandable = !!details;
  return (
    <div className={`fl ${child ? 'fl--child' : ''} ${tone ? `fl--${tone}` : ''} ${open ? 'is-open' : ''}`}>
      <button className="fl__row" onClick={() => expandable && setOpen((o) => !o)} disabled={!expandable} aria-expanded={expandable ? open : undefined}>
        <span className="fl__time">{time}</span>
        <span className="fl__actor">
          {glyph && <span className="fl__glyph">{glyph}</span>}
          {actor}
        </span>
        <span className="fl__what">{children}</span>
        {expandable && <span className="fl__more" aria-hidden>{open ? '–' : '+'}</span>}
      </button>
      {open && details && <div className="fl__details">{details}</div>}
    </div>
  );
}

function Child({ ev, state }: { ev: FeedEvent; state: PanelState }) {
  const time = relTime(ev.ts, state.startTs);
  switch (ev.type) {
    case 'tool_call': {
      const res = state.toolResults[ev.data.id];
      return (
        <Line
          child
          time={time}
          actor={ev.data.actor === 'agent' ? 'Agent' : 'Policy'}
          tone={res && !res.ok ? 'critical' : ev.data.actor === 'agent' ? 'accent' : undefined}
          details={<pre>{json(ev.data.args)}{res ? `\n→ ${json(res.result)}` : ''}</pre>}
        >
          {toolLine(ev.data.tool, ev.data.args)}
          {res && !res.ok ? ' (failed)' : ''}
        </Line>
      );
    }
    case 'blocked_by_policy': {
      const d = ev.data;
      const what = TOOL_TRY[d.tool] ?? d.tool.replace(/_/g, ' ');
      return (
        <Line child time={time} actor="Policy" tone="critical" details={<pre>{json({ reason: d.reason, args: d.args })}</pre>}>
          Blocked: {d.actor} tried to {what}
          {d.required_level > d.level ? `, needs level ${d.required_level} (now ${d.level})` : `: ${d.reason}`}
        </Line>
      );
    }
    case 'pii_guard': {
      const d = ev.data;
      const what = d.kind === 'card' ? 'card number' : d.kind === 'account' ? 'account number' : 'digits';
      const text =
        d.action === 'redacting'
          ? `Hid granny's ${what} from the caller`
          : d.action === 'redacted'
            ? `${what[0].toUpperCase()}${what.slice(1)} hidden (${d.digits_hidden ?? '?'} digits${d.held_ms != null ? `, ${(d.held_ms / 1000).toFixed(1)} s` : ''}) · call continues`
            : d.action === 'muted'
              ? "Cut granny's mic before the digits reached the caller"
              : d.action === 'extended'
                ? 'Kept the mic cut: more digits'
                : 'Mic restored';
      return (
        <Line
          child
          time={time}
          actor="Guard"
          tone={d.action === 'unmuted' ? 'quiet' : d.action === 'redacted' ? 'accent' : 'critical'}
          details={
            <p>
              {d.reason ?? d.action}
              {d.matched ? ` · heard “${d.matched}”` : ''}
              {d.dropped_frames ? ` · ${d.dropped_frames} audio frames dropped` : ''}
            </p>
          }
        >
          {text}
        </Line>
      );
    }
    case 'agent_thought':
      return (
        <Line child time={time} actor="Thinking" tone="quiet" details={<p>{ev.data.text}</p>}>
          <i>{ev.data.text}</i>
        </Line>
      );
    default:
      return null;
  }
}

function Group({ item, state }: { item: Extract<Item, { kind: 'group' }>; state: PanelState }) {
  const d = item.head.data;
  const raised = d.level > d.prev_level;
  const lowered = d.level < d.prev_level;
  return (
    <div className={`fg ${raised ? `fg--up fg--l${d.level}` : ''}`}>
      <Line
        time={relTime(item.head.ts, state.startTs)}
        actor="Policy"
        tone={raised || lowered ? undefined : 'quiet'}
        details={
          <dl>
            <dt>Rule</dt>
            <dd className="mono">{d.rule_id}</dd>
            <dt>Turn</dt>
            <dd>{d.turn}</dd>
            <dt>Score</dt>
            <dd className="num">{Math.round((d.inputs?.score ?? 0) * 100)}%</dd>
            {d.inputs?.red_flags?.length ? (
              <>
                <dt>Red flags</dt>
                <dd>{d.inputs.red_flags.map((f) => f.replace(/_/g, ' ')).join(', ')}</dd>
              </>
            ) : null}
            <dt>Allowed</dt>
            <dd>{(d.allowed_tools || []).join(', ') || 'none'}</dd>
          </dl>
        }
      >
        {raised || lowered ? (
          <>
            <span className={`lvlword lvlword--${d.level}`}>{raised ? 'Raised' : 'Lowered'} to {LEVEL_NAMES[d.level]}</span>
            <span className="fl__sep"> · </span>
            {d.description}
          </>
        ) : (
          <>
            {d.description}
            {item.repeat > 1 && <span className="fl__count"> ×{item.repeat}</span>}
          </>
        )}
      </Line>
      {item.children.map((c) => (
        <Child key={c.seq} ev={c} state={state} />
      ))}
    </div>
  );
}

function ShieldCheck() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden>
      <path fill="currentColor" d="M12 2 4 5v6c0 5 3.4 9.7 8 11 4.6-1.3 8-6 8-11V5l-8-3zm-1.2 14.2-3.5-3.5 1.4-1.4 2.1 2.1 4.6-4.6 1.4 1.4-6 6z" />
    </svg>
  );
}

/** The transparency moment: the guardian wasn't sure, asked, judged, decided. Always expanded. */
function VerifyCard({ item, state }: { item: Extract<Item, { kind: 'verify' }>; state: PanelState }) {
  const verdict = [...item.events].reverse().find((e) => VERDICTS.has(e.data.stage));
  const tone = verdict ? (verdict.data.stage === 'passed' ? 'ok' : verdict.data.stage === 'failed' ? 'critical' : 'warn') : 'pending';
  return (
    <div className={`vcard vcard--${tone}`}>
      <div className="vcard__head">
        <ShieldCheck />
        <span>Verifying identity</span>
        {!verdict && <span className="vcard__pending">in progress</span>}
      </div>
      <ol className="vcard__steps">
        {item.events.map((e) => {
          const d = e.data;
          let text: string;
          let cls = '';
          switch (d.stage) {
            case 'started':
              text = `Not sure yet: ${d.reason ?? 'the claim can’t be confirmed'}`;
              break;
            case 'asked':
              text = `Asked the caller: “${d.question ?? ''}”`;
              break;
            case 'answered':
              text = `Caller said: “${d.answer ?? ''}”${d.match != null ? ` · match ${Math.round(d.match * 100)}%` : ''}`;
              break;
            case 'passed':
              text = `Identity verified${d.reason ? ` · ${d.reason}` : ''}`;
              cls = 'ok';
              break;
            case 'failed':
              text = `Failed verification${d.reason ? ` · ${d.reason}` : ''}, treating as scam`;
              cls = 'critical';
              break;
            case 'timeout':
              text = `No answer${d.reason ? ` · ${d.reason}` : ''}`;
              cls = 'warn';
              break;
            default:
              text = d.reason ?? '';
          }
          return (
            <li key={e.seq} className={`vcard__step ${cls}`}>
              <span className="fl__time">{relTime(e.ts, state.startTs)}</span>
              <span>{text}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Single({ ev, state }: { ev: FeedEvent; state: PanelState }) {
  const time = relTime(ev.ts, state.startTs);
  switch (ev.type) {
    case 'supervisor': {
      const d = ev.data;
      return (
        <Line
          time={time}
          actor="Supervisor"
          glyph={SUP_GLYPH[d.kind]}
          tone={d.severity === 'critical' ? 'critical' : d.severity === 'warn' ? 'warn' : undefined}
          details={d.detail && Object.keys(d.detail).length ? <pre>{json(d.detail)}</pre> : undefined}
        >
          {d.message}
        </Line>
      );
    }
    case 'memory_match': {
      const d = ev.data;
      return (
        <Line
          time={time}
          actor="Memory"
          tone="accent"
          details={
            <>
              {d.excerpt && <p>Earlier call: “{d.excerpt}”</p>}
              <p>
                <a href={`/report/${encodeURIComponent(d.past_call_id)}`} target="_blank" rel="noreferrer">
                  Open earlier report
                </a>
              </p>
            </>
          }
        >
          Matches a scam I stopped earlier ({Math.round(d.similarity * 100)}% similar)
        </Line>
      );
    }
    case 'guardian_ask': {
      const d = ev.data;
      const outcome = d.action === 'hung_up' ? 'hung up for her' : d.action === 'refused' ? 'declined' : 'answered privately';
      return (
        <Line time={time} actor="Guardian" tone="accent" details={<p>Answer: “{d.answer}”</p>}>
          Granny asked “{d.question}”: {outcome}
        </Line>
      );
    }
    case 'level_change':
      return (
        <Line time={time} actor="Policy">
          <span className={`lvlword lvlword--${ev.data.to}`}>Level {LEVEL_NAMES[ev.data.to]}</span>
        </Line>
      );
    default:
      return <Child ev={ev} state={state} />;
  }
}

export function DecisionFeed({ state }: { state: PanelState }) {
  const items = buildItems(state.feed);
  const ref = useAutoScroll<HTMLDivElement>(state.feed.length);
  return (
    <div className="feed" ref={ref}>
      {!items.length && <p className="empty">Decisions appear here as the guardian acts.</p>}
      {items.map((it) =>
        it.kind === 'group' ? (
          <Group key={it.key} item={it} state={state} />
        ) : it.kind === 'verify' ? (
          <VerifyCard key={it.key} item={it} state={state} />
        ) : (
          <Single key={it.key} ev={it.ev} state={state} />
        ),
      )}
    </div>
  );
}
