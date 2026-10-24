"""The agent's tools over MCP (FastMCP), bound to the live call. Same gate: an external agent can't skip the policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp import FastMCP

from .agent.tools import run_tool

if TYPE_CHECKING:
    from .hub import Hub


def build(hub: Hub) -> FastMCP:
    mcp = FastMCP("granny-protect-5000")

    async def call(tool: str, **args) -> dict:
        return await run_tool(hub.session, tool, args, actor="mcp")

    @mcp.tool
    async def whisper_to_granny(text: str, tone: str = "caution") -> dict:
        """Speak a short private warning to the protected person only."""
        return await call("whisper_to_granny", text=text, tone=tone)

    @mcp.tool
    async def vibrate_granny() -> dict:
        """Vibrate the protected person's phone."""
        return await call("vibrate_granny")

    @mcp.tool
    async def mute_granny_to_caller() -> dict:
        """Cut the protected person's audio to the caller (PII guard only)."""
        return await call("mute_granny_to_caller")

    @mcp.tool
    async def unmute_granny() -> dict:
        """Restore the protected person's audio to the caller."""
        return await call("unmute_granny")

    @mcp.tool
    async def challenge_caller(question: str) -> dict:
        """Ask the caller a verification question out loud."""
        return await call("challenge_caller", question=question)

    @mcp.tool
    async def terminate_call(reason: str) -> dict:
        """End the call for both sides (level 3 only)."""
        return await call("terminate_call", reason=reason)

    @mcp.tool
    async def notify_family(summary: str) -> dict:
        """Send a short alert to the family contact."""
        return await call("notify_family", summary=summary)

    @mcp.tool
    async def log_event(event: str) -> dict:
        """Write a note to the call log."""
        return await call("log_event", event=event)

    @mcp.tool
    async def call_status() -> dict:
        """Current call state, level, unlocked tools, and latest signals (read-only)."""
        from .policy.engine import allowed_tools

        s = hub.session
        return {"call_id": s.id, "state": s.state, "level": s.policy.level, "allowed_tools": allowed_tools(s.policy),
                "signals": s.last_signals.model_dump() if s.last_signals else None}

    return mcp
