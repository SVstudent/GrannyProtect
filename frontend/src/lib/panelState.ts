// Shared reducer for /panel and /stage. Dedupes by seq, resets when call_id changes.
import { useEffect, useReducer } from 'react';
import type { CallStatus, Heartbeat, Level, PanelDataMap, PanelEvent, PanelEventOf, Speaker, VerificationStage } from './types';
import { getJson, useSocket } from './ws';

export interface Utterance {
  key: string;
  speaker: Speaker;
  utt: number | string;
  text: string;
  partial: boolean;
  refined: boolean;
  source?: string;
  hits: string[];
  seq: number;
}

export type FeedEvent = PanelEventOf<
  'rule_fired' | 'tool_call' | 'tool_result' | 'blocked_by_policy' | 'pii_guard' | 'agent_thought' | 'info' | 'level_change' | 'supervisor' | 'guardian_ask' | 'memory_match' | 'verification'
>;

export interface PanelState {
  callId: string;
  callState: CallStatus | 'unknown';
  endedBy?: string;
  level: Level;
  levelSeq: number;
  seen: Set<number>;
  signals: Record<string, PanelDataMap['signals'] & { seq: number }>;
  transcript: Utterance[];
  feed: FeedEvent[];
  toolResults: Record<string, PanelDataMap['tool_result']>;
  lastWhisper: { text: string; seq: number } | null;
  reportCallId: string | null;
  micCut: boolean;
  /** Private granny↔guardian exchanges, merged into the transcript by seq. */
  privates: (PanelDataMap['guardian_ask'] & { seq: number })[];
  claimedOrg: string | null;
  /** Latest identity-check stage for this call, if any. */
  verifyStage: VerificationStage | null;
  /** What the guardian said out loud on the call (e.g. the verification question). */
  guardianSaid: { seq: number; text: string }[];
  /** First timestamp seen for this call (epoch seconds), for m:ss labels. */
  startTs: number | null;
  /** Latest supervisor heartbeat. Survives every reset. */
  heartbeat: Heartbeat | null;
}

export const initialPanel = (): PanelState => ({
  callId: '',
  callState: 'unknown',
  level: 0,
  levelSeq: 0,
  seen: new Set(),
  signals: {},
  transcript: [],
  feed: [],
  toolResults: {},
  lastWhisper: null,
  reportCallId: null,
  micCut: false,
  privates: [],
  claimedOrg: null,
  verifyStage: null,
  guardianSaid: [],
  startTs: null,
  heartbeat: null,
});

type Action = { kind: 'event'; ev: PanelEvent } | { kind: 'reset' } | { kind: 'heartbeat'; hb: Heartbeat };

function upsertUtt(list: Utterance[], u: Partial<Utterance> & { speaker: Speaker; utt: number | string }, seq: number): Utterance[] {
  const key = `${u.speaker}:${u.utt}`;
  const i = list.findIndex((x) => x.key === key);
  if (i === -1) {
    return [
      ...list,
      { key, speaker: u.speaker, utt: u.utt, text: u.text ?? '', partial: u.partial ?? true, refined: !!u.refined, source: u.source, hits: [], seq },
    ];
  }
  const prev = list[i];
  // A late partial must not overwrite a final.
  if (u.partial && !prev.partial) return list;
  const next = list.slice();
  next[i] = { ...prev, ...u, key, refined: prev.refined || !!u.refined, hits: prev.hits };
  return next;
}

function reduceEvent(s: PanelState, ev: PanelEvent): PanelState {
  // Heartbeats are global and unstored: never reset, never dedupe.
  if (ev.type === 'heartbeat') return { ...s, heartbeat: ev.data };
  if (ev.call_id && s.callId && ev.call_id !== s.callId) s = { ...initialPanel(), heartbeat: s.heartbeat };
  if (typeof ev.seq === 'number' && s.seen.has(ev.seq)) return s;
  const seen = new Set(s.seen);
  if (typeof ev.seq === 'number') seen.add(ev.seq);
  const t = toEpoch(ev.ts);
  s = { ...s, seen, callId: ev.call_id || s.callId, startTs: s.startTs ?? t };

  switch (ev.type) {
    case 'call_state':
      return { ...s, callState: ev.data.state, endedBy: ev.data.by };
    case 'transcript_partial':
      return { ...s, transcript: upsertUtt(s.transcript, { ...ev.data, partial: true }, ev.seq) };
    case 'transcript_final': {
      const d = ev.data;
      return {
        ...s,
        transcript: upsertUtt(s.transcript, { speaker: d.speaker, utt: d.utt, text: d.text, partial: false, refined: d.refined, source: d.source }, ev.seq),
      };
    }
    case 'fast_path': {
      const matches = (ev.data.hits || []).map((h) => h.match).filter(Boolean);
      if (!matches.length) return s;
      const t = s.transcript.slice();
      for (let i = t.length - 1; i >= 0; i--) {
        if (t[i].speaker === ev.data.speaker) {
          t[i] = { ...t[i], hits: Array.from(new Set([...t[i].hits, ...matches])) };
          break;
        }
      }
      return { ...s, transcript: t };
    }
    case 'signals':
      return {
        ...s,
        signals: { ...s.signals, [ev.data.provider]: { ...ev.data, seq: ev.seq } },
        claimedOrg: ev.data.claimed_org || s.claimedOrg,
      };
    case 'guardian_ask':
      return { ...s, feed: [...s.feed, ev], privates: [...s.privates, { ...ev.data, seq: ev.seq }] };
    case 'memory_match':
      return { ...s, feed: [...s.feed, ev] };
    case 'verification': {
      const d = ev.data;
      return {
        ...s,
        feed: [...s.feed, ev],
        verifyStage: d.stage,
        guardianSaid: d.stage === 'asked' && d.question ? [...s.guardianSaid, { seq: ev.seq, text: d.question }] : s.guardianSaid,
      };
    }
    case 'rule_fired':
      return {
        ...s,
        feed: [...s.feed, ev],
        level: ev.data.level,
        levelSeq: ev.data.level !== s.level ? ev.seq : s.levelSeq,
      };
    case 'level_change':
      return { ...s, feed: [...s.feed, ev], level: ev.data.to, levelSeq: ev.data.to !== s.level ? ev.seq : s.levelSeq };
    case 'tool_call': {
      const a = ev.data.args || {};
      const isWhisper = ev.data.tool === 'whisper_to_granny';
      const text = isWhisper ? String(a.text ?? a.message ?? '') : '';
      return { ...s, feed: [...s.feed, ev], lastWhisper: isWhisper && text ? { text, seq: ev.seq } : s.lastWhisper };
    }
    case 'tool_result':
      return { ...s, feed: [...s.feed, ev], toolResults: { ...s.toolResults, [ev.data.id]: ev.data } };
    case 'blocked_by_policy':
    case 'supervisor':
    case 'agent_thought':
    case 'info':
      return { ...s, feed: [...s.feed, ev] };
    case 'pii_guard':
      // Mic-cut indicator: on while muted/extended/redacting; off on redacted/unmuted.
      return { ...s, feed: [...s.feed, ev], micCut: ev.data.action === 'muted' || ev.data.action === 'extended' || ev.data.action === 'redacting' };
    case 'report_ready':
      return { ...s, reportCallId: ev.data.call_id || ev.call_id };
    default:
      return s;
  }
}

function reducer(s: PanelState, a: Action): PanelState {
  if (a.kind === 'reset') return { ...initialPanel(), heartbeat: s.heartbeat };
  if (a.kind === 'heartbeat') return { ...s, heartbeat: a.hb };
  return reduceEvent(s, a.ev);
}

export function usePanelFeed() {
  const { sock, connected } = useSocket('panel');
  const [state, dispatch] = useReducer(reducer, undefined, initialPanel);
  useEffect(() => {
    if (!sock) return;
    return sock.onJson((msg: PanelEvent) => {
      if (msg && typeof msg === 'object' && 'type' in msg && 'data' in msg) dispatch({ kind: 'event', ev: msg });
    });
  }, [sock]);
  // Seed the supervisor strip before the first 2 s heartbeat arrives.
  useEffect(() => {
    getJson<Heartbeat>('/api/shift').then((hb) => {
      if (hb && typeof hb === 'object' && 'mode' in hb) dispatch({ kind: 'heartbeat', hb });
    });
  }, []);
  return { state, connected, clear: () => dispatch({ kind: 'reset' }) };
}

/** Pick the signals reading to show: latest non-regex model reading, else regex. */
export function pickSignals(s: PanelState) {
  const all = Object.values(s.signals);
  const model = all.filter((x) => x.provider !== 'regex').sort((a, b) => b.seq - a.seq)[0];
  const regex = s.signals['regex'];
  return { shown: model ?? regex ?? null, regex: regex ?? null, isFastPath: !model && !!regex };
}

/** Epoch seconds from a number (s or ms) or ISO string; null if unparseable. */
export function toEpoch(ts: number | string | null | undefined): number | null {
  if (ts == null || ts === '') return null;
  if (typeof ts === 'number') return ts > 1e12 ? ts / 1000 : ts;
  const n = Date.parse(ts);
  return isNaN(n) ? null : n / 1000;
}

/** m:ss since the call's first event. */
export function relTime(ts: number | string, startTs: number | null): string {
  const t = toEpoch(ts);
  if (t == null || startTs == null) return '';
  const d = Math.max(0, Math.round(t - startTs));
  return `${Math.floor(d / 60)}:${String(d % 60).padStart(2, '0')}`;
}
