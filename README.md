# Granny-Protect-5000

An invisible guardian on every call. It listens, warns granny privately, cuts her mic before a number gets out, and hangs up when the danger is too great. It also supervises itself, so it can stay on duty for hours with no one watching. Everything it does is logged with the rule that allowed it.

**The models are probabilistic, but the decisions aren't.** Models produce typed signals. A written rule table (`backend/app/policy/rules.yaml`) turns those signals into actions. The agent can only use the tools the rules have unlocked at that moment.

## Run the demo (two devices, live voice)

```bash
make install && make build        # once
make lan                          # prints the three URLs below
```

`make lan` serves HTTPS straight from this laptop on the local Wi-Fi or a phone hotspot. Audio never leaves the room, so a weak internet connection can't make the call choppy. Only the small model calls go out to TokenRouter.

| Device | Open | Notes |
|---|---|---|
| Granny's phone (Android Chrome) | `https://<laptop-ip>:8443/granny` | Tap **Advanced → Proceed** once (self-signed cert), then **Answer** |
| Scammer's laptop or phone | `https://<laptop-ip>:8443/caller` | Tap **Call Grandma** |
| Projector | `https://<laptop-ip>:8443/stage` (or `/panel`) | Press **H** to show "this is all granny experiences" |

Backup if the devices can't share a network: `make backend`, then `make tunnel` (Cloudflare), or run `ngrok http 8000`. Tunnel audio quality depends on the venue's internet. Measured here: LAN gave 12.6 s of 14 s received with no gaps; a slow hotel-style connection through the tunnel halved it.

Before going on stage, run `make e2e E2E_BASE=https://<laptop-ip>:8443`. Two real Chrome phones call each other with live audio, and the run checks every step below.

## The workflow judges see (about 2 minutes)
1. **The call opens.** "Hi, this is Mark from Chase's fraud department…" The guardian notices the authority claim within milliseconds and whispers *"Be careful, this might not be who they say."* The scammer hears nothing.
2. **Granny asks the guardian.** She says *"Guardian, is this real?"* Her mic is cut before the caller hears it (0.00 s leaked in testing). She hears privately: *"They say they're Chase, but I can't confirm that…"*
3. **The ask.** "I just need to verify your account number." TypeSafe Jev scores the call (about 1.2 s), and rule R2 fires. Her phone vibrates and she hears *"They're asking for your private details. Chase never calls for that. Just say: I'll call you back myself."*
4. **Number redaction.** "Let me find it… it's four, seven, two, nine… is that right, dear?" The caller hears "Let me find it… it's", then silence for **only the digits**, then "is that right, dear?". The call carries on normally. She hears *"I kept your account number private."* This works for bank account and card numbers, and the caller's subtitles show `••••`. If the scammer asks again and she starts again, that's a second attempt and the guardian hangs up.
5. **The pressure, then the hang-up.** "Don't tell your family, or your account will be frozen." The guardian hangs up. The scammer hears *"This call has been ended by Granny-Protect-5000."* Granny hears *"I've ended this call. It was a scam. You did nothing wrong."*, then *"If you're worried, call Chase at the number on the back of your card."*
6. **Aftermath.** An incident report opens, with a "What to do now" step, the transcript, every decision and its rule, and the cost (about $0.0003 for the call). The guardian remembers the script, so the next caller who uses it is recognized on the first line.

Every line granny hears is pre-generated in a warm, natural voice (55 lines, cached in `backend/.cache`; run `make voices` to rebuild). There's no robotic fallback once the cache is warm.

**Live subtitles** run under both phones so judges can read the conversation. The scammer's subtitles never show granny's private questions, the guardian's whispers, or her digits.

**Call quality:** 40 ms audio chunks, an adaptive jitter buffer, an anti-aliasing filter on the mic, and *no* added delay on ordinary calls. The 700 ms protection delay switches on only once a call turns risky.

## Trusted-person alerts (Telegram)
When the call reaches Alert, the guardian messages a loved one: *"⚠️ Grandma may be getting scammed right now"*, with who the caller claims to be, what they want, and the scam likelihood. After the hang-up it sends *"✅ Scam call stopped · Grandma is safe"* with what the scammer got (nothing) and the next step.

Setup takes about 2 minutes:
1. In Telegram, message **@BotFather** → `/newbot` → copy the token into `.env` as `TELEGRAM_BOT_TOKEN=…` (and set `PROTECTED_NAME=Grandma`).
2. The loved one opens the new bot and sends it any message ("hi"). The chat id is discovered automatically.
3. Restart the server, then run `curl -k -X POST https://localhost:8443/api/telegram/test`. Their phone should buzz.

`GET /api/telegram` shows the setup status.

## Unsupervised: the guardian supervises itself
| Question | How it knows | What it does |
|---|---|---|
| **Overspending?** | Every model call is priced: Jev reports its exact `usage.cost`, and chat models are priced from token usage. Budgets are tracked per call and per shift. | At 80% of the shift budget, or once the call budget is spent: **economy** (LLM agent paused, Jev kept). At 100%: **rules-only** (regex + policy). Protection never stops; spending does. |
| **Looping?** | Repeated identical tool calls, whisper spam (more than 2 in 10 s), the agent retrying refused tools | Blocks the duplicate, holds the whisper so granny isn't overwhelmed, suspends the agent until the level changes |
| **Stuck?** | Per-provider circuit breakers, slow calls, voice audio with no transcript for 7 s, a decision backlog | Falls back Jev → LLM → regex. **Switches a phone whose browser speech recognition stalled to server-side transcription** and back when it recovers. Skips stale turns. |
| **Should stop?** | 3 min of silence, a call over 30 min, a phone gone for 15 s, autopilot rules | Ends the call cleanly. Autopilot stops itself on shift end, budget, 3 failures in a row, or 3 self-check misses in a row. |

**Autopilot shift** (panel drawer, or `POST /api/autopilot {"minutes":120}`) keeps the guardian working between live calls. It replays test calls through the real pipeline, grades itself, and steps aside the moment a real call comes in. A real run: 8 calls, 100% accuracy, $0.0026, then it stopped itself.

## Pipeline (one TokenRouter key)
| Stage | Provider | Fallback |
|---|---|---|
| Live words (PII guard, instant) | Chrome Web Speech on each phone | Server transcription on stall, then typed lines (mock) |
| Accurate transcript | TokenRouter `mistralai/voxtral-small-24b-2507` | Browser text |
| Typed decisions | TokenRouter `typesafe/jev-1.14` (noul / score / choice) | LLM JSON, then regex |
| Agent and report | TokenRouter `openai/gpt-4o-mini`, tool calling | Deterministic agent |
| Granny's voice | TokenRouter `openai/gpt-audio-mini`, pre-generated and transcript-verified | Best on-device voice |
| Family alert | Discord webhook (`DISCORD_WEBHOOK_URL`) | Logged only |

## Escalation ladder (`rules.yaml`)
| Level | Rules | Granny hears | Scammer hears |
|---|---|---|---|
| 0 Monitor | default | nothing | the normal call |
| 1 Caution | one red flag, or score ≥ 0.5 | "Be careful, this might not be who they say." | nothing |
| 2 Alert | two red flags, score ≥ 0.8, model says alert plus a flag, or a remembered scam script | a warning naming who they claim to be, plus one safe sentence to say; vibration | nothing |
| PII guard | at L1 or above she starts saying digits (pre-armed once credentials are requested) | "I stopped that, so they didn't hear your number." | silence |
| 3 Terminate | at alert: score ≥ 0.98, or secrecy or a threat; a 2nd PII attempt, or granny says "Guardian, hang up" | "I've ended this call…" plus how to check safely | "This call has been ended by Granny-Protect-5000." |

The keyword fast path sees no negation, so on its own it can't raise past Caution.

## Agent tools (gated by level, also served over MCP at `/mcp`)
`whisper_to_granny`, `vibrate_granny`, `mute_granny_to_caller`, `unmute_granny`, `challenge_caller`, `terminate_call`, `notify_family`, `log_event`, `write_incident_report`. Any call the policy hasn't unlocked comes back as **blocked by policy** on the panel.

## Tests
- `make test`: 40 tests covering the policy, regex, the supervisor (budget modes, loops, circuit breakers), the guardian ask, scam memory, the whisper echo filter, and all 6 scripted calls.
- `make e2e`: real Chrome phones with live audio. It checks two-way speech (transcribed back word for word), mic cuts, whispers, the hang-up, and the report.
- `e2e/voice_only_e2e.py`: the scammer only *speaks*, with no speech recognition in the browser. The guardian self-heals to server transcription and still hangs up.

## Demo tips
- Wear headphones on both phones. Only granny's audio goes to the room speakers.
- While a whisper plays, speech on granny's side that sounds like the whisper is ignored, but her own words still count.
- A phone page opened on a second device takes the line, and the old one shows "Open on another device".
- Scam memory learns from real calls, including rehearsals. Before the stage demo, clear it so the first call climbs the whole ladder: `curl -k -X DELETE https://<laptop-ip>:8443/api/memory`. (The e2e tests clean up after themselves.)

## Privacy
No audio is stored. Only the transcript and decision log (SQLite) are kept, for the report. A production version would announce the AI at the start of the call (California requires all-party consent to recording).
