// Types mirroring PROTOCOL.md exactly.

export type CallStatus = 'idle' | 'ringing' | 'active' | 'ended';
export type SttMode = 'mock' | 'browser' | 'hybrid' | 'deepgram';
export type Speaker = 'caller' | 'granny';
export type Tone = 'caution' | 'alert' | 'pii' | 'end';
export type Level = 0 | 1 | 2 | 3;

export const LEVEL_NAMES = ['Monitor', 'Caution', 'Alert', 'Terminate'] as const;

export const SIGNAL_NAMES = [
  'claims_authority',
  'requests_credentials',
  'requests_unusual_payment',
  'creates_urgency',
  'demands_secrecy',
  'threatens',
] as const;
export type SignalName = (typeof SIGNAL_NAMES)[number];

// ---- Phone sockets: client -> server ----
export type PhoneClientMsg =
  | { type: 'dial' }
  | { type: 'answer' }
  | { type: 'hangup' }
  | { type: 'stt'; final: boolean; text: string }
  | { type: 'say'; text: string }
  | { type: 'whisper_done'; id: string };

// ---- Phone sockets: server -> client ----
export interface StateMsg {
  type: 'state';
  call: CallStatus;
  call_id: string;
  stt_mode: SttMode;
  /** Mic chunk size in ms (default 40). */
  audio_frame_ms?: number;
}
export interface WhisperMsg {
  type: 'whisper';
  id: string;
  text: string;
  tone: Tone;
  audio_b64: string | null;
  mime: string;
}
export interface VibrateMsg {
  type: 'vibrate';
  pattern: number[];
}
export interface CaptionMsg {
  type: 'caption';
  speaker: 'granny';
  text: string;
}
export interface AgentSayMsg {
  type: 'agent_say';
  text: string;
  audio_b64: string | null;
  mime: string;
}
export interface CallEndedMsg {
  type: 'call_ended';
  by: 'agent' | 'caller' | 'granny';
  message: string;
  /** Not in PROTOCOL.md's table, but played if the backend sends it. */
  audio_b64?: string | null;
  mime?: string;
}
/** Live subtitle line. Partials replace the line for speaker+utt; finals commit it. */
export interface SubtitleMsg {
  type: 'subtitle';
  speaker: 'caller' | 'granny' | 'guardian';
  text: string;
  final: boolean;
  utt: number;
  /** Granny talking to the guardian (granny's page only). */
  private?: boolean;
  /** Digits already masked as "••••" (caller's page). */
  redacted?: boolean;
}
export interface SttRestartMsg {
  type: 'stt_restart';
}
export type PhoneServerMsg = StateMsg | WhisperMsg | VibrateMsg | CaptionMsg | AgentSayMsg | CallEndedMsg | SttRestartMsg | SubtitleMsg;

// ---- Identity verification (v4) ----
export type VerificationStage = 'started' | 'asked' | 'answered' | 'passed' | 'failed' | 'timeout';
export type VerificationResult = 'passed' | 'failed' | 'timeout';

// ---- Supervisor ----
export type SupervisorKind = 'budget' | 'loop' | 'stuck' | 'recovered' | 'mode' | 'stop' | 'autopilot';
export type Severity = 'info' | 'warn' | 'critical';
export type SupervisorMode = 'full' | 'economy' | 'rules-only';
export type ProviderState = 'ok' | 'degraded' | 'open';
export interface ProviderHealth {
  state: ProviderState;
  p50_ms: number | null;
  calls: number;
  failures: number;
}
export interface AutopilotStatus {
  running: boolean;
  started: number | null;
  minutes: number | null;
  calls: number;
  accuracy: number | null;
  stop_reason: string | null;
}
export interface Heartbeat {
  uptime_s: number;
  calls: number;
  scams_stopped: number;
  pii_blocked: number;
  loops_caught: number;
  recoveries: number;
  spend_usd: number;
  spend_per_hour: number;
  shift_budget_usd: number;
  call_spend_usd: number;
  call_budget_usd: number;
  mode: SupervisorMode;
  mode_reason?: string | null;
  providers: Record<string, ProviderHealth>;
  autopilot?: AutopilotStatus | null;
}

// ---- Panel socket ----
export interface SignalReading {
  p: number;
  value?: unknown;
  evidence?: string | string[] | null;
}

export interface PanelDataMap {
  call_state: { state: CallStatus; by?: string };
  transcript_partial: { speaker: Speaker; text: string; utt: number | string };
  transcript_final: {
    speaker: Speaker;
    text: string;
    utt: number | string;
    source: 'browser' | 'tokenrouter' | 'mock' | 'deepgram';
    refined: boolean;
  };
  fast_path: {
    speaker: Speaker;
    hits: { kind: string; match: string }[];
    digit_run?: number | string | null;
    latency_ms: number;
  };
  signals: {
    turn: number;
    provider: string;
    latency_ms: number;
    scam_score: number;
    recommended_level: Level;
    signals: Partial<Record<SignalName, SignalReading>> & Record<string, SignalReading>;
    rationale?: string;
    /** Organization the caller claims to represent, e.g. "Chase". */
    claimed_org?: string | null;
  };
  rule_fired: {
    turn: number;
    rule_id: string;
    description: string;
    level: Level;
    prev_level: Level;
    inputs: { score: number; red_flags: string[]; pii_attempts: number };
    allowed_tools: string[];
    actions: string[];
  };
  level_change: { from: Level; to: Level; name: string };
  tool_call: { id: string; actor: 'policy' | 'agent'; tool: string; args: Record<string, unknown> };
  tool_result: { id: string; tool: string; ok: boolean; result: unknown };
  blocked_by_policy: {
    actor: 'policy' | 'agent';
    tool: string;
    args: Record<string, unknown>;
    reason: string;
    level: Level;
    required_level: Level;
  };
  pii_guard: {
    action: 'muted' | 'unmuted' | 'extended' | 'redacting' | 'redacted';
    reason?: string;
    /** redacting / redacted: what kind of number is being hidden. */
    kind?: 'card' | 'account' | 'number';
    /** redacted: how many digits were hidden and for how long. */
    digits_hidden?: number;
    held_ms?: number;
    matched?: string;
    attempt?: number;
    dropped_frames?: number;
  };
  agent_thought: { text: string; provider: string };
  report_ready: { call_id: string; url: string };
  info: { message: string };
  supervisor: { kind: SupervisorKind; severity: Severity; message: string; detail?: Record<string, unknown> };
  heartbeat: Heartbeat;
  /** Granny asked the guardian privately; her mic was cut so the caller didn't hear. */
  guardian_ask: { question: string; answer: string; action: 'answered' | 'hung_up' | 'refused'; level: Level };
  /** Identity check when the guardian is uncertain (v4). The real answer is never sent. */
  verification: {
    stage: VerificationStage;
    kind: 'family' | 'callback';
    question?: string;
    answer?: string;
    /** 0–1 similarity of the caller's answer to the accepted answers. */
    match?: number;
    reason?: string;
  };
  /** The current call matches a scam script stopped on an earlier call. */
  memory_match: { similarity: number; past_call_id: string; past_when_ts?: number | string | null; excerpt?: string | null };
}

export type PanelEventType = keyof PanelDataMap;

export type PanelEvent = {
  [K in PanelEventType]: { type: K; seq: number; ts: number | string; call_id: string; data: PanelDataMap[K] };
}[PanelEventType];

export type PanelEventOf<K extends PanelEventType> = Extract<PanelEvent, { type: K }>;

// ---- REST ----
export interface ConfigResp {
  stt_provider: string;
  signals_provider: string;
  agent_provider: string;
  tts_provider: string;
  challenge_mode: boolean;
  pii_delay_ms: number;
  models?: { agent?: string; signals?: string; stt?: string; tts?: string };
  studio_whispers?: number;
}
export interface ScriptInfo {
  id: string;
  title: string;
  kind: 'legit' | 'scam';
  expected_max_level: Level;
}
export interface CallSummary {
  call_id: string;
  started: number | string;
  ended: number | string | null;
  outcome: string | null;
  max_level: Level;
}
export interface Report {
  call_id: string;
  started: number | string;
  ended: number | string | null;
  outcome: string | null;
  max_level: Level;
  summary: string;
  recommendations: string[];
  timeline: { ts: number | string; kind: string; text: string }[];
  signals_peak: Record<string, number>;
  actions: { ts: number | string; actor: string; tool: string; args: Record<string, unknown>; ok: boolean }[];
  excerpts: { speaker: Speaker; text: string }[];
  cost_usd?: number | null;
  claimed_org?: string | null;
  safe_next_step?: string | null;
  verification?: {
    kind: 'family' | 'callback';
    question?: string | null;
    answer?: string | null;
    match?: number | null;
    result: VerificationResult | string;
    reason?: string | null;
  } | null;
  memory_matches?: { similarity: number; past_call_id: string; past_when_ts?: number | string | null; excerpt?: string | null }[];
  supervisor?: { ts: number | string; kind: SupervisorKind; severity: Severity; message: string }[];
  /** True while the call is still happening (the loved one's live link); the page refreshes itself. */
  live?: boolean;
  /** What happened, in plain English. */
  story?: { ts: number | string; text: string }[];
}
