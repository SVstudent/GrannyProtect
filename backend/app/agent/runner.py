"""The LLM agent. It writes the words (whisper lines, report) and calls tools, but only the policy unlocks tools.

Providers: mock (deterministic canned behaviour, used in tests and the zero-key demo) and llm
(any tool-calling chat model through TokenRouter's OpenAI-compatible endpoint).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING

from ..config import settings
from ..supervisor import price, sup
from ..policy.engine import LEVEL_NAMES, Decision
from .tools import TOOL_SPECS, run_tool

if TYPE_CHECKING:
    from ..call import CallSession

SYSTEM = """You are Granny-Protect-5000, an invisible guardian on a live phone call protecting an elderly person ("granny") from scams.
A deterministic policy engine has already decided the protection level and runs the required actions. Your job:
- When asked to, write ONE short whisper for granny (max 20 words): calm, kind, specific to what the caller is asking for, never scary or shaming.
- Optionally use other tools that help. Tools the policy has not unlocked will be refused with "blocked_by_policy"; accept that and do not retry.
- Before acting, write one short sentence of reasoning as plain text.
Never speak to the caller except via challenge_caller."""


def _context(session: CallSession, decision: Decision, task: str) -> str:
    sig = session.last_signals
    sig_txt = ", ".join(f"{k}={v.p:.2f}" for k, v in sig.signals.items()) if sig else "none"
    return (
        f"TASK: {task}\n\n"
        f"Level: {decision.level} ({LEVEL_NAMES[decision.level]}), entered via rule {decision.rule_id}: {decision.description}\n"
        f"Signals: {sig_txt}; scam_score={sig.scam_score if sig else 0}\n"
        f"Tools unlocked right now: {', '.join(decision.allowed_tools)}\n"
        f"Challenge mode: {'on' if session.challenge_mode else 'off'}\n\n"
        f"Transcript (most recent last):\n{session.transcript_text(14)}"
    )


async def on_level(session: CallSession, decision: Decision, write_whisper: bool) -> str | None:
    """Run the agent after the policy escalates. Returns the whisper text it delivered, if any."""
    task = (
        "The policy just raised the level. Write and deliver the alert whisper with whisper_to_granny (tone 'alert'), "
        "specific to what the caller wants."
        if write_whisper
        else "The policy just raised the level and already whispered its fixed warning, so do NOT whisper again. "
        "Decide whether another tool helps (for example challenge_caller if challenge mode is on); otherwise reply with one sentence and no tools."
    )
    try:
        if decision.level >= 3:
            # The call is already over; there is nothing left for a model to decide.
            await session.emit("agent_thought", provider="policy",
                               text=f"Rule {decision.rule_id} unlocked termination and the policy ended the call. Granny heard a reassurance.")
            return None
        if session.agent_suspended_level is not None and session.agent_suspended_level == decision.level:
            return None
        if settings.agent_provider == "llm" and settings.llm_api_key:
            if sup.allow("agent"):
                t0 = time.perf_counter()
                try:
                    out, cost = await asyncio.wait_for(_llm_loop(session, _context(session, decision, task)), settings.agent_timeout_s)
                except Exception as e:  # noqa: BLE001
                    await sup.failure("agent", e, "deterministic agent lines")
                    return await _mock(session, decision, write_whisper, eager=False)
                await sup.record("agent", cost, (time.perf_counter() - t0) * 1000, session.id)
                return out
            await session.emit("agent_thought", provider="supervisor",
                               text=f"LLM agent paused ({sup.mode}: {sup.mode_reason}); using deterministic lines.")
            return await _mock(session, decision, write_whisper, eager=False)
        return await _mock(session, decision, write_whisper)
    except Exception as e:  # noqa: BLE001
        await session.emit("agent_thought", text=f"Agent unavailable ({type(e).__name__}); the policy's fixed lines stand in.", provider="system")
        return None


async def _mock(session: CallSession, decision: Decision, write_whisper: bool, eager: bool = True) -> str | None:
    """Deterministic agent. `eager` (zero-key mock only) also tries an early hang-up so the policy gate is visible."""
    sig = session.last_signals
    p = (lambda n: sig.p(n)) if sig else (lambda n: 0.0)
    said = None
    if decision.level == 1:
        await session.emit("agent_thought", provider="mock",
                           text="One red flag so far. The policy delivered the caution whisper; I'm watching for a credential or payment request.")
        if session.challenge_mode:
            await run_tool(session, "challenge_caller",
                           {"question": "Before we continue, could you give me a callback number I can check on your official website?"})
    elif decision.level == 2:
        if p("requests_unusual_payment") >= 0.6:
            said = "They want gift cards or a wire transfer. Real companies never ask for that. Please don't pay."
        elif p("requests_credentials") >= 0.6:
            said = "They're asking for your bank details. Your bank never calls for that. Don't share it."
        else:
            said = "This sounds like a scam. You can hang up. I'm right here with you."
        await session.emit("agent_thought", provider="mock",
                           text="Two red flags together; this matches a bank-impersonation pattern. Tailoring the alert to what they're asking for. I'd like to hang up now.")
        if write_whisper:
            await run_tool(session, "whisper_to_granny", {"text": said, "tone": "alert"})
        if eager:
            # The agent is eager; the policy keeps it honest. This shows up as BLOCKED BY POLICY on the panel.
            await run_tool(session, "terminate_call", {"reason": "Scam pattern detected"})
            await session.emit("agent_thought", provider="mock",
                               text="Termination refused: the policy needs coercion or a higher score first. Staying on guard.")
    elif decision.level == 3:
        await session.emit("agent_thought", provider="mock",
                           text=f"Rule {decision.rule_id} unlocked termination and the policy ended the call. Granny heard a reassurance.")
    return said


def _openai_tools() -> list[dict]:
    return [{"type": "function", "function": spec} for spec in TOOL_SPECS if spec["name"] != "write_incident_report"]


async def _llm_loop(session: CallSession, context: str) -> tuple[str | None, float]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=settings.agent_timeout_s)
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": context}]
    whispered = None
    cost = 0.0
    for _ in range(3):
        resp = await client.chat.completions.create(
            model=settings.agent_model, messages=messages, tools=_openai_tools(), temperature=0.3, max_tokens=300
        )
        cost += price(settings.agent_model, resp.usage)
        msg = resp.choices[0].message
        if msg.content:
            await session.emit("agent_thought", text=msg.content.strip(), provider=settings.agent_model)
        if not msg.tool_calls:
            break
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if session.agent_suspended_level == session.policy.level:
                result = {"ok": False, "error": "blocked_by_supervisor: agent suspended for repeating itself"}
            elif tc.function.name == "whisper_to_granny" and whispered:
                result = {"ok": False, "error": "one whisper per turn"}
            else:
                result = await run_tool(session, tc.function.name, args, actor="agent")
                if tc.function.name == "whisper_to_granny" and result.get("ok"):
                    whispered = args.get("text")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
        if session.agent_suspended_level == session.policy.level:
            break
    return whispered, cost


async def write_summary(session: CallSession) -> dict:
    """LLM-written summary and recommendations for the incident report, with a deterministic fallback."""
    fallback = {
        "summary": session.short_summary(),
        "recommendations": [
            "Call the bank or agency back using the number on the card or official website.",
            "Never share account numbers, PINs, or one-time codes on an inbound call.",
            "Report scam calls to the FTC at reportfraud.ftc.gov and to the FBI IC3 at ic3.gov.",
        ],
    }
    if settings.agent_provider != "llm" or not settings.llm_api_key or not sup.allow("agent"):
        return fallback
    from openai import AsyncOpenAI

    try:
        t0 = time.perf_counter()
        # After the call latency doesn't matter, so the report gets a much longer budget than live turns.
        client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=25)
        resp = await asyncio.wait_for(client.chat.completions.create(
            model=settings.agent_model,
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=500,
            messages=[
                {"role": "system", "content": "You write incident reports for a scam-call protection agent, ready to file with the FTC or a bank. "
                 'Reply JSON: {"summary": "<3-4 factual sentences: who the caller claimed to be, what they asked for, what the agent did>", '
                 '"recommendations": ["<3 short next steps for the family>"]}'},
                {"role": "user", "content": f"Outcome: {session.outcome}. Max level {session.max_level}.\n"
                 f"Actions: {json.dumps(session.actions, default=str)[:3000]}\n\nTranscript:\n{session.transcript_text(40)}"},
            ],
        ), 27)
        await sup.record("agent", price(settings.agent_model, resp.usage), (time.perf_counter() - t0) * 1000, session.id)
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return {"summary": data.get("summary") or fallback["summary"],
                "recommendations": data.get("recommendations") or fallback["recommendations"]}
    except Exception as e:  # noqa: BLE001
        await sup.failure("agent", e, "the deterministic report summary")
        return fallback
