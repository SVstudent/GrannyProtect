"""The panel event schema. Everything the judges see on the agent-mind panel is one of these."""

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "call_state",          # ringing / active / ended
    "transcript_partial",  # live words as they are spoken
    "transcript_final",    # a finished turn
    "fast_path",           # regex hits on partials (<5 ms)
    "signals",             # typed decision-model output for a caller turn
    "rule_fired",          # policy decision: rule id, level, allowed tools
    "level_change",
    "tool_call",           # actor = policy | agent
    "tool_result",
    "blocked_by_policy",
    "pii_guard",
    "agent_thought",
    "report_ready",
    "verification",        # identity check when the guardian is uncertain: started / asked / answered / verdict
    "guardian_ask",        # granny spoke privately to the guardian ("Guardian, is this real?")
    "memory_match",        # caller repeats a scam script stopped on an earlier call
    "supervisor",          # the agent watching itself: budget, loops, stalls, mode changes, stops
    "heartbeat",           # supervisor status every 2 s (broadcast only, never stored)
    "info",
]


class PanelEvent(BaseModel):
    type: EventType
    seq: int = 0
    ts: float = Field(default_factory=time.time)
    call_id: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
