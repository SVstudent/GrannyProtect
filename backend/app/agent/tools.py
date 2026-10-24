"""Agent tools and the policy gate. The agent and the policy call the same tools, and both go through the gate."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any


from ..config import settings
from ..policy.engine import LEVEL_NAMES, allowed_tools, required_level
from ..supervisor import sup
from ..tts import whispers

if TYPE_CHECKING:
    from ..call import CallSession

_ids = itertools.count(1)

TOOL_SPECS: list[dict] = [
    {
        "name": "whisper_to_granny",
        "description": "Speak a short, calm, kind private warning into the protected person's ear only. The caller never hears it. Max ~20 words.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "tone": {"type": "string", "enum": ["caution", "alert", "pii", "end"]}},
            "required": ["text"],
        },
    },
    {
        "name": "verify_caller",
        "description": "When it's unclear whether the caller is who they claim, ask them one identity question out loud "
                       "(a family fact only the real relative knows, or whether she may call back on the official number).",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"},
                                                        "kind": {"type": "string", "enum": ["family", "callback"]}},
                       "required": ["question"]},
    },
    {"name": "vibrate_granny", "description": "Vibrate the protected person's phone to get attention.", "parameters": {"type": "object", "properties": {}}},
    {
        "name": "redact_sensitive_number",
        "description": "Silence ONLY the account or card number granny is reading out; the rest of the call continues. "
                       "Driven by the live transcript, unlocked only while she is reading digits.",
        "parameters": {"type": "object", "properties": {"kind": {"type": "string", "enum": ["account", "card", "number"]}}},
    },
    {"name": "end_redaction", "description": "Give granny's voice back to the caller after a redacted number.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "mute_granny_to_caller", "description": "Cut the protected person's audio to the caller (PII guard).", "parameters": {"type": "object", "properties": {}}},
    {"name": "unmute_granny", "description": "Restore the protected person's audio to the caller.", "parameters": {"type": "object", "properties": {}}},
    {
        "name": "challenge_caller",
        "description": "Ask the caller a verification question out loud, such as a callback number on the official website. Real institutions can answer; scammers dodge.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    },
    {
        "name": "terminate_call",
        "description": "End the call for both sides. The caller hears a closing message and the protected person hears a reassurance.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
    },
    {
        "name": "notify_family",
        "description": "Message the protected person's trusted loved one (Telegram) that a scam is in progress.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"},
                                                        "stage": {"type": "string", "enum": ["alert", "ended"]}}},
    },
    {
        "name": "log_event",
        "description": "Write a note to the call log.",
        "parameters": {"type": "object", "properties": {"event": {"type": "string"}}, "required": ["event"]},
    },
    {"name": "write_incident_report", "description": "Write the post-call incident report.", "parameters": {"type": "object", "properties": {}}},
]


async def run_tool(session: CallSession, tool: str, args: dict | None = None, actor: str = "agent") -> dict[str, Any]:
    """Gate, execute, and log one tool call. Returns the result dict (or an error for a blocked call)."""
    args = args or {}
    allowed = allowed_tools(session.policy)
    if tool not in allowed or (tool == "challenge_caller" and not session.challenge_mode):
        need = required_level(tool)
        if session.policy.call_ended:
            reason = "the call has already ended"
        elif tool == "challenge_caller" and not session.challenge_mode and tool in allowed:
            reason = "challenge mode is off"
        elif tool in ("mute_granny_to_caller", "unmute_granny", "redact_sensitive_number", "end_redaction"):
            reason = "mic controls unlock only while the PII guard is firing"
        elif tool == "write_incident_report":
            reason = "reports are written after the call ends"
        else:
            reason = f"{tool} requires level {need} ({LEVEL_NAMES[need]}); current level {session.policy.level} ({LEVEL_NAMES[session.policy.level]})"
        await session.emit("blocked_by_policy", actor=actor, tool=tool, args=args, reason=reason,
                           level=session.policy.level, required_level=need)
        if actor == "agent":
            await sup.agent_refused(session, tool)
        return {"ok": False, "error": f"blocked_by_policy: {reason}"}
    if loop := await sup.check_tool(session, tool, args, actor):
        return {"ok": False, "error": f"blocked_by_supervisor: {loop}"}

    call_id = f"t{next(_ids)}"
    await session.emit("tool_call", id=call_id, actor=actor, tool=tool, args=args)
    try:
        result = await _IMPL[tool](session, **args)
        ok = True
    except Exception as e:  # noqa: BLE001
        result, ok = {"error": repr(e)}, False
    await session.emit("tool_result", id=call_id, tool=tool, ok=ok, result=result)
    session.actions.append({"ts": session.now_wall(), "actor": actor, "tool": tool, "args": args, "ok": ok})
    return {"ok": ok, **(result if isinstance(result, dict) else {"result": result})}


async def _whisper(session: CallSession, text: str, tone: str = "caution", duck: bool = True) -> dict:
    import time

    session.whisper_times.append(time.monotonic())
    wid = await session.whisper(text, tone, duck=duck)
    return {"whisper_id": wid, "studio_audio": whispers.cached_audio(text) is not None}


async def _vibrate(session: CallSession) -> dict:
    await session.send_granny({"type": "vibrate", "pattern": [400, 150, 400, 150, 400]})
    return {"vibrated": True}


async def _mute(session: CallSession) -> dict:
    dropped = session.set_muted(True)
    return {"muted": True, "dropped_frames": dropped}


async def _redact(session: CallSession, kind: str = "number") -> dict:
    dropped = session.start_cut(settings.redact_lead_ms / 1000)
    return {"redacting": kind, "dropped_frames": dropped}


async def _end_redaction(session: CallSession) -> dict:
    session.end_cut(settings.redact_tail_ms / 1000)
    return {"restored": True}


async def _unmute(session: CallSession) -> dict:
    session.set_muted(False)
    return {"muted": False}


async def _verify(session: CallSession, question: str, kind: str = "family") -> dict:
    await session.ask_caller(question)
    return {"asked": question, "kind": kind}


async def _challenge(session: CallSession, question: str) -> dict:
    await session.send_caller({"type": "agent_say", "text": question, "audio_b64": None, "mime": "audio/mpeg"})
    return {"asked": question}


async def _terminate(session: CallSession, reason: str = "") -> dict:
    await session.terminate(reason)
    return {"terminated": True, "reason": reason}


async def _notify(session: CallSession, summary: str = "", stage: str = "alert") -> dict:
    """Message the trusted person (Telegram/Discord). One message per stage: 'alert' during, 'ended' after."""
    from .. import notify

    stage = "ended" if stage == "ended" else "alert"
    if session.virtual:
        # Replays and autopilot practice calls must never wake up a real loved one.
        return {"sent": False, "stage": stage, "preview": notify.build_message(session, stage),
                "note": "practice call: loved one not messaged"}
    if stage in session.family_alerts:
        return {"sent": False, "note": f"loved one already got the {stage} message for this call"}
    session.family_alerts.add(stage)
    session.family_notified = True
    return await notify.send(session, stage)


async def _log(session: CallSession, event: str) -> dict:
    await session.emit("info", message=event)
    return {"logged": True}


async def _report(session: CallSession) -> dict:
    report = await session.build_report()
    return {"call_id": report["call_id"], "outcome": report["outcome"]}


_IMPL = {
    "whisper_to_granny": _whisper,
    "vibrate_granny": _vibrate,
    "redact_sensitive_number": _redact,
    "end_redaction": _end_redaction,
    "mute_granny_to_caller": _mute,
    "unmute_granny": _unmute,
    "challenge_caller": _challenge,
    "verify_caller": _verify,
    "terminate_call": _terminate,
    "notify_family": _notify,
    "log_event": _log,
    "write_incident_report": _report,
}
