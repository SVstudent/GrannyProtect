"""The supervisor: the agent watching itself, so it can run unattended for hours.

It answers four questions continuously:
  overspending?  every model call is priced (Jev returns exact cost; chat models are priced from token usage).
                 Near the call or shift budget it drops to economy (no LLM agent, no STT refine), and at the
                 shift cap it drops to rules-only (regex + policy), so protection never stops, but spending does.
  looping?       repeated identical tool calls, whisper spam, and an agent retrying refused tools are caught
                 and blocked; the agent is suspended until the level changes.
  stuck?         per-provider circuit breakers (consecutive failures or timeouts open the circuit and route
                 to the fallback), STT stalls (speech audio but no transcript, so the phone is told to restart
                 recognition), stale decision backlogs (coalesced), and dead or abandoned calls.
  should stop?   abandoned calls, calls over the max duration, lost phones, and autopilot stop rules
                 (duration, budget, consecutive errors, accuracy drift).
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import settings

if TYPE_CHECKING:
    from .call import CallSession
    from .hub import Hub

# USD per 1M tokens (input, output). Estimates for token-priced models; Jev reports its exact cost.
PRICES = {
    "openai/gpt-4o-mini": (0.15, 0.60),
    "mistralai/voxtral-small-24b-2507": (0.10, 0.30),
    "openai/gpt-audio-mini": (0.60, 20.0),  # audio output tokens dominate
}
DEFAULT_PRICE = (0.50, 1.50)

PROVIDERS = ("jev", "agent", "stt", "tts")


def price(model: str, usage: Any) -> float:
    if usage is None:
        return 0.0
    p_in, p_out = PRICES.get(model, DEFAULT_PRICE)
    tin = getattr(usage, "prompt_tokens", 0) or 0
    tout = getattr(usage, "completion_tokens", 0) or 0
    return (tin * p_in + tout * p_out) / 1_000_000


@dataclass
class Health:
    state: str = "ok"  # ok | degraded | open
    calls: int = 0
    failures: int = 0
    consecutive: int = 0
    opened_at: float = 0.0
    latencies: deque = field(default_factory=lambda: deque(maxlen=30))

    def view(self) -> dict:
        p50 = int(statistics.median(self.latencies)) if self.latencies else None
        return {"state": self.state, "p50_ms": p50, "calls": self.calls, "failures": self.failures}


class Supervisor:
    OPEN_AFTER = 2         # consecutive failures before a circuit opens
    RETRY_AFTER_S = 45.0   # half-open retry window
    SLOW_MS = {"jev": 6000, "agent": 9000, "stt": 20000, "tts": 120000}

    def __init__(self) -> None:
        self.hub: Hub | None = None
        self.started = time.time()
        self.spend = 0.0
        self.call_spend: dict[str, float] = {}
        self.call_budget = float(getattr(settings, "call_budget_usd", 0.05))
        self.shift_budget = float(getattr(settings, "shift_budget_usd", 2.0))
        self.health = {p: Health() for p in PROVIDERS}
        self.calls = 0
        self.scams_stopped = 0
        self.pii_blocked = 0
        self.loops_caught = 0
        self.recoveries = 0
        self.mode = "full"
        self.mode_reason = "within budget"
        self.autopilot: dict = {"running": False, "started": None, "minutes": 0, "calls": 0, "accuracy": None, "stop_reason": None}
        self._task: asyncio.Task | None = None

    # ---------- events ----------
    def _session(self) -> CallSession | None:
        return self.hub.session if self.hub else None

    async def note(self, kind: str, severity: str, message: str, session: CallSession | None = None, **detail) -> None:
        s = session or self._session()
        print(f"[supervisor] {severity.upper()} {kind}: {message}", flush=True)
        if s is not None:
            await s.emit("supervisor", kind=kind, severity=severity, message=message, detail=detail)

    # ---------- budget ----------
    async def record(self, provider: str, usd: float, latency_ms: float, call_id: str | None) -> None:
        h = self.health[provider]
        h.calls += 1
        h.latencies.append(latency_ms)
        was = h.state
        h.consecutive = 0
        if latency_ms > self.SLOW_MS[provider]:
            h.state = "degraded"
            await self.note("stuck", "warn", f"{provider} is slow ({latency_ms / 1000:.1f}s); keeping it under watch")
        elif was != "ok":
            h.state = "ok"
            self.recoveries += 1
            msg = "circuit closed, back on the primary path" if was == "open" else "back to normal"
            await self.note("recovered", "info", f"{provider} is healthy again; {msg}")
        self.spend += usd
        if call_id:
            self.call_spend[call_id] = self.call_spend.get(call_id, 0.0) + usd
        await self._update_mode(call_id)

    async def _update_mode(self, call_id: str | None) -> None:
        call = self.call_spend.get(call_id or "", 0.0)
        if self.spend >= self.shift_budget:
            mode, reason = "rules-only", f"shift budget ${self.shift_budget:.3f} spent; regex + policy keep protecting for free"
        elif self.spend >= 0.8 * self.shift_budget:
            mode, reason = "economy", f"shift spend at {self.spend / self.shift_budget:.0%} of budget; LLM agent paused, Jev kept"
        elif call >= self.call_budget:
            mode, reason = "economy", f"this call spent ${call:.4f} of its ${self.call_budget:.2f} budget"
        else:
            mode, reason = "full", "within budget"
        if mode != self.mode:
            sev = {"full": "info", "economy": "warn", "rules-only": "critical"}[mode]
            self.mode, self.mode_reason = mode, reason
            await self.note("mode", sev, f"Mode → {mode.upper()}: {reason}", spend=round(self.spend, 4))

    async def set_budget(self, call_usd: float | None, shift_usd: float | None) -> None:
        if call_usd is not None:
            self.call_budget = max(0.0, call_usd)
        if shift_usd is not None:
            self.shift_budget = max(0.0, shift_usd)
        await self.note("budget", "info", f"Budgets set: ${self.call_budget:.3f}/call, ${self.shift_budget:.3f}/shift")
        await self._update_mode(self._session().id if self._session() else None)

    def allow(self, provider: str) -> bool:
        """May we spend on this provider right now? (mode + circuit breaker)"""
        if provider in ("agent", "stt") and self.mode != "full":
            return False
        if provider == "jev" and self.mode == "rules-only":
            return False
        h = self.health[provider]
        if h.state == "open" and time.monotonic() - h.opened_at < self.RETRY_AFTER_S:
            return False
        return True

    async def failure(self, provider: str, err: BaseException, fallback: str) -> None:
        h = self.health[provider]
        h.calls += 1
        h.failures += 1
        h.consecutive += 1
        if h.consecutive >= self.OPEN_AFTER and h.state != "open":
            h.state, h.opened_at = "open", time.monotonic()
            await self.note("stuck", "critical",
                            f"{provider} failed {h.consecutive}× in a row ({type(err).__name__}); circuit open, routing to {fallback} "
                            f"for {int(self.RETRY_AFTER_S)}s", error=repr(err)[:200])
        else:
            if h.state == "ok":
                h.state = "degraded"
            await self.note("stuck", "warn", f"{provider} call failed ({type(err).__name__}); using {fallback}", error=repr(err)[:200])

    # ---------- loops ----------
    async def check_tool(self, session: CallSession, tool: str, args: dict, actor: str) -> str | None:
        """Return a reason to block a repeated/looping tool call, or None."""
        if actor == "policy":
            return None  # policy actions are deterministic and already de-duplicated by the ladder
        now = time.monotonic()
        if session.agent_suspended_level is not None and session.agent_suspended_level == session.policy.level:
            return "agent suspended for repeating itself; resumes when the level changes"
        key = (tool, tuple(sorted((k, str(v).strip().lower()) for k, v in args.items())))
        last = session.tool_seen.get(key)
        session.tool_seen[key] = now
        if last is not None and now - last < 60:
            self.loops_caught += 1
            await self.note("loop", "warn", f"Agent repeated {tool} with the same arguments; blocked the duplicate", session, tool=tool)
            return "duplicate of a call made moments ago"
        if tool == "whisper_to_granny":
            recent = [t for t in session.whisper_times if now - t < 10]
            if len(recent) >= 2:
                self.loops_caught += 1
                await self.note("loop", "warn", "Whisper fatigue: 2 whispers in the last 10s already; holding this one so granny isn't overwhelmed", session)
                return "too many whispers in a short time"
        return None

    async def agent_refused(self, session: CallSession, tool: str) -> None:
        session.agent_blocks[tool] = session.agent_blocks.get(tool, 0) + 1
        if session.agent_blocks[tool] >= 2 and session.agent_suspended_level != session.policy.level:
            session.agent_suspended_level = session.policy.level
            self.loops_caught += 1
            await self.note("loop", "warn", f"Agent keeps retrying refused {tool}; suspending its tool calls until the level changes",
                            session, tool=tool)

    # ---------- call lifecycle ----------
    async def on_call_end(self, session: CallSession) -> None:
        if session.virtual and not self.autopilot["running"]:
            pass
        self.calls += 1
        if session.ended_by == "agent":
            self.scams_stopped += 1
        self.pii_blocked += session.policy.pii_attempts

    def call_cost(self, call_id: str) -> float:
        return round(self.call_spend.get(call_id, 0.0), 6)

    # ---------- watchdog + heartbeat ----------
    def heartbeat(self) -> dict:
        s = self._session()
        return {
            "uptime_s": int(time.time() - self.started),
            "calls": self.calls, "scams_stopped": self.scams_stopped, "pii_blocked": self.pii_blocked,
            "loops_caught": self.loops_caught, "recoveries": self.recoveries,
            "spend_usd": round(self.spend, 5),
            "spend_per_hour": round(self.spend / max((time.time() - self.started) / 3600, 1 / 60), 4),
            "shift_budget_usd": self.shift_budget,
            "call_spend_usd": self.call_cost(s.id) if s else 0.0,
            "call_budget_usd": self.call_budget,
            "mode": self.mode, "mode_reason": self.mode_reason,
            "providers": {p: h.view() for p, h in self.health.items()},
            "autopilot": dict(self.autopilot),
        }

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(1.0)
            tick += 1
            try:
                await self._watch()
                if tick % 2 == 0 and self.hub:
                    s = self.hub.session
                    await self.hub.broadcast({"type": "heartbeat", "seq": 0, "ts": time.time(), "call_id": s.id,
                                              "data": self.heartbeat()})
            except Exception as e:  # noqa: BLE001 - the watchdog itself must never die
                print(f"[supervisor] watchdog error: {e!r}", flush=True)

    async def _watch(self) -> None:
        s = self._session()
        if s is None or s.state != "active" or s.virtual:
            return
        now = time.monotonic()
        # STT stall: we hear voiced audio from a speaker but get no transcript from their phone.
        if settings.stt_provider in ("browser", "hybrid") and settings.llm_api_key:
            for who in ("caller", "granny"):
                voiced, heard = s.voiced_at[who], s.last_speech[who]
                if (voiced and now - voiced < 3 and now - max(heard, s.answered_mono) > 7 and now - s.stt_restart_at[who] > 20
                        and not s.server_stt[who]):
                    s.stt_restart_at[who] = now
                    await self.hub.send_json(who, {"type": "stt_restart"})
                    s.server_stt[who] = True
                    self.recoveries += 1
                    await self.note("stuck", "warn", f"{who}'s speech recognition stalled (audio but no words for 7s). Restarting it, "
                                                     "and transcribing on the server until it's back", s)
        # A phone vanished mid-call.
        for who in ("caller", "granny"):
            if self.hub.phones.get(who) is None:
                if not s.lost_at.get(who):
                    s.lost_at[who] = now
                elif now - s.lost_at[who] > 15:
                    await self.note("stop", "warn", f"{who}'s phone disconnected for 15s; ending the call cleanly", s)
                    await s.hangup(who)
                    return
            else:
                s.lost_at[who] = 0.0
        # Abandoned call / runaway duration.
        last_any = max(s.last_speech["caller"], s.last_speech["granny"], s.answered_mono)
        if now - last_any > settings.max_silence_s:
            await self.note("stop", "info", f"No one has spoken for {int(settings.max_silence_s)}s; ending the abandoned call", s)
            await s.hangup("caller")
        elif now - s.answered_mono > settings.max_call_s:
            await self.note("stop", "warn", f"Call passed the {int(settings.max_call_s / 60)} min limit; ending it", s)
            await s.hangup("caller")


sup = Supervisor()
