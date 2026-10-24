# Granny-Protect-5000 wire protocol

Backend: FastAPI on `:8000`. It serves `frontend/dist` from the same origin, so one HTTPS tunnel covers everything.
In dev, Vite on `:5173` proxies `/api` and `/ws` (with ws:true) to `:8000`.

Level names: `0 Monitor`, `1 Caution`, `2 Alert`, `3 Terminate`.

## Phone sockets: `/ws/caller` and `/ws/granny`

### Client → server
| Message | Meaning |
|---|---|
| binary | Int16LE PCM, mono, **16 kHz**, 20 ms frames (320 samples). The client downsamples in the AudioWorklet. |
| `{"type":"dial"}` | Caller starts the call, and granny's phone rings. |
| `{"type":"answer"}` | Granny answers, and the call becomes active. |
| `{"type":"hangup"}` | Either side hangs up. |
| `{"type":"stt","final":bool,"text":str}` | A browser Web Speech result. Partials carry the cumulative text of the current utterance. Sent only when `stt_mode` is `browser` or `hybrid`. |
| `{"type":"say","text":str}` | Mock mode only. A typed line; the server simulates word-by-word partials, then a final. |
| `{"type":"whisper_done","id":str}` | Granny's page finished playing a whisper. |

### Server → client
| Message | Meaning |
|---|---|
| binary | The other side's audio, in the same 16 kHz Int16LE format. |
| `{"type":"state","call":"idle"\|"ringing"\|"active"\|"ended","call_id":str,"stt_mode":"mock"\|"browser"\|"hybrid"\|"deepgram"}` | Call status. |
| `{"type":"whisper","id","text","tone":"caution"\|"alert"\|"pii"\|"end","audio_b64":str\|null,"mime":"audio/mpeg"}` | Granny only. Play a short tone, then the audio, or `speechSynthesis(text)` when `audio_b64` is null. Send `whisper_done` afterwards. |
| `{"type":"vibrate","pattern":[ms...]}` | Granny only. Calls `navigator.vibrate(pattern)`. |
| `{"type":"caption","speaker":"granny","text":str}` | Caller only. What the caller "hears" in mock mode, one chunk at a time. Words cut by the PII guard never arrive. |
| `{"type":"agent_say","text","audio_b64":str\|null,"mime"}` | Caller only. The agent speaks to the caller (challenge question or closing line). |
| `{"type":"call_ended","by":"agent"\|"caller"\|"granny","message":str}` | Both sides. |

## Panel socket: `/ws/panel` (server → client only)
On connect, the server replays the current call's events, then streams live ones.
Every message is a `PanelEvent`: `{"type", "seq", "ts", "call_id", "data"}`.

| type | data |
|---|---|
| `call_state` | `{state, by?}` |
| `transcript_partial` | `{speaker:"caller"\|"granny", text, utt}` |
| `transcript_final` | `{speaker, text, utt, source:"browser"\|"tokenrouter"\|"mock"\|"deepgram", refined:bool}`. A later event with the same `speaker`+`utt` and `refined:true` replaces the text with the TokenRouter STT version. |
| `fast_path` | `{speaker, hits:[{kind, match}], digit_run, latency_ms}` |
| `signals` | `{turn, provider, latency_ms, scam_score, recommended_level, signals:{name:{p, value, evidence}}, rationale}`. Signal names: `claims_authority, requests_credentials, requests_unusual_payment, creates_urgency, demands_secrecy, threatens`. |
| `rule_fired` | `{turn, rule_id, description, level, prev_level, inputs:{score, red_flags:[...], pii_attempts}, allowed_tools:[...], actions:[...]}` |
| `level_change` | `{from, to, name}` |
| `tool_call` | `{id, actor:"policy"\|"agent", tool, args}` |
| `tool_result` | `{id, tool, ok, result}` |
| `blocked_by_policy` | `{actor, tool, args, reason, level, required_level}` |
| `pii_guard` | `{action:"muted"\|"unmuted"\|"extended", reason, matched, attempt, dropped_frames}` |
| `agent_thought` | `{text, provider}` |
| `report_ready` | `{call_id, url}`. Report JSON is at `GET /api/calls/{call_id}/report`. |
| `info` | `{message}` |

## REST
- `GET  /api/config` → `{stt_provider, signals_provider, agent_provider, tts_provider, challenge_mode, pii_delay_ms}`
- `POST /api/challenge` with body `{"enabled":bool}`
- `GET  /api/scripts` → `[{id, title, kind:"legit"|"scam", expected_max_level}]`
- `POST /api/replay/{id}` plays a script as a virtual call, streamed to the panel
- `POST /api/reset` returns to an idle call
- `GET  /api/calls` → `[{call_id, started, ended, outcome, max_level}]`
- `GET  /api/calls/{id}/report` → `{call_id, started, ended, outcome, max_level, summary, recommendations:[str], timeline:[{ts, kind, text}], signals_peak:{name:p}, actions:[{ts, actor, tool, args, ok}], excerpts:[{speaker, text}]}`

## Supervisor (Unsupervised track): added after v1

The supervisor watches the agent itself: spend, provider health, loops, and stalls. It switches modes and stops work on its own.

### New panel event types
| type | data |
|---|---|
| `supervisor` | `{kind:"budget"\|"loop"\|"stuck"\|"recovered"\|"mode"\|"stop"\|"autopilot", severity:"info"\|"warn"\|"critical", message:str, detail:{...}}`. Stored with the call, so it appears in the decision feed and the report. |
| `heartbeat` | Every 2 s and not stored. See the shape below. **Never reset panel state on a `heartbeat`, whatever its call_id.** |

Heartbeat `data`:
```json
{
  "uptime_s": 5400, "calls": 87, "scams_stopped": 41, "pii_blocked": 39, "loops_caught": 6, "recoveries": 3,
  "spend_usd": 0.183, "spend_per_hour": 0.122, "shift_budget_usd": 2.0,
  "call_spend_usd": 0.0021, "call_budget_usd": 0.05,
  "mode": "full" | "economy" | "rules-only",
  "mode_reason": "shift spend at 80% of budget",
  "providers": {
    "jev":   {"state": "ok" | "degraded" | "open", "p50_ms": 1130, "calls": 120, "failures": 0},
    "agent": {...}, "stt": {...}, "tts": {...}
  },
  "autopilot": {"running": true, "started": 1792800000.0, "minutes": 120, "calls": 34, "accuracy": 1.0, "stop_reason": null}
}
```

### New phone message (server → client)
- `{"type":"stt_restart"}`: the watchdog saw speech audio but no transcripts. Stop and restart Web Speech recognition.

### New REST endpoints
- `GET  /api/shift` returns the heartbeat data.
- `POST /api/budget` with body `{"call_usd": number?, "shift_usd": number?}`. The panel's "Squeeze budget" demo button sends `{"shift_usd": <current spend + 0.002>}`, and the panel's "Reset budget" sends `{"shift_usd": 2.0}`.
- `POST /api/autopilot` with body `{"minutes": number}` starts an unattended shift. `POST /api/autopilot/stop` stops it.

### Report additions
`cost_usd`, plus `supervisor:[{ts, kind, severity, message}]`.

## Number redaction, subtitles and call quality (v3)
- **Redaction (bank account and card numbers only).** Once the call is risky (level ≥ 1, or the caller asked for credentials), the caller hears silence for **exactly** the span of digits granny reads out. Everything before and after still goes through.
  - The server cuts a capture-time window that starts `REDACT_LEAD_MS` (600) before the first digit was recognized and ends `REDACT_TAIL_MS` (350) before the first non-digit word.
  - Panel `pii_guard` events: `action:"redacting"` with `{kind:"card"|"account"|"number"}`, then `action:"redacted"` with `{kind, digits_hidden, held_ms}`.
  - Granny then hears "I kept your card/account number private." This whisper doesn't duck her audio, so the conversation keeps flowing.
  - Tools: `redact_sensitive_number(kind)`, `end_redaction()`. Both are policy-gated and unlocked only while digits are being read.
- **Subtitles.** Phones receive `{"type":"subtitle","speaker":"caller"|"granny"|"guardian","text","final","utt","private","redacted"}`.
  - Granny's page gets everything, including `guardian` whisper lines (`private:true`).
  - The caller's page never gets granny's private questions or whispers, and once the call is risky her number runs arrive as `••••` (`redacted:true`).
- **Latency.**
  - `state.audio_frame_ms` (default 40) sets the mic chunk size; `AUDIO_FRAME_MS=100` suits slow tunnels.
  - Granny→caller audio has **no added delay on an ordinary call**. The `PII_DELAY_MS` protection delay switches on only once the call is risky.

## Identity verification when the guardian is uncertain (v4)
When a caller's claim can't be confirmed or ruled out, the guardian asks one verification question instead of guessing. Typical case: someone claims to be family and asks for money, but nothing proves it's a scam. Clear-cut scams skip this and follow the normal protocol.

**New panel event `verification`:** `{stage, kind:"family"|"callback", question?, answer?, match?, reason}`
- `stage:"started"`, with `reason` explaining why the guardian was uncertain (score, flags, claim).
- `stage:"asked"` carries the question spoken to the caller.
- `stage:"answered"` carries `answer` (what the caller said) and `match` (0–1 similarity to the accepted answers). The real answer is never sent to any screen.
- `stage:"passed" | "failed" | "timeout"` carries `reason` in plain English (e.g. "Answered 'Biscuit', matching the family fact" or "Said they don't remember").

**Phones**
- The caller gets `{"type":"agent_say","text","audio_b64","mime"}`, the question spoken out loud. Both phones also get a `subtitle` with `speaker:"guardian"` and `private:false` for that question, since it's said on the call.
- Granny gets the usual private whispers ("I'm asking a family question to make sure it's really him", "He answered correctly. It's really him", "He couldn't answer. This is a scam").

**Policy**
- `rules.yaml → verification` decides when the guardian is "uncertain".
- A failed check fires rule `R3f_failed_verification` (hang up). A passed check stops soft escalation; hard rules such as coercion still apply.
- Family questions live in `backend/family_facts.yaml`. `GET /api/facts` returns the questions only, never the answers.
