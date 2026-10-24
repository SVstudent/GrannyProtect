"""Deterministic policy engine. Same signals in, same decision out, every time."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..signals.model import TurnSignals

LEVEL_NAMES = ["Monitor", "Caution", "Alert", "Terminate"]
POLICY = yaml.safe_load((Path(__file__).parent / "rules.yaml").read_text())


@dataclass
class PolicyState:
    level: int = 0
    pii_attempts: int = 0
    pii_guard_active: bool = False
    call_ended: bool = False
    memory_match: bool = False
    granny_hangup: bool = False
    verified: bool = False
    verification_failed: bool = False
    verification_unanswered: bool = False


@dataclass
class Decision:
    rule_id: str
    description: str
    prev_level: int
    level: int
    target_level: int
    capped: bool
    inputs: dict
    allowed_tools: list[str]
    actions: list[dict] = field(default_factory=list)

    @property
    def escalated(self) -> bool:
        return self.level > self.prev_level


def red_flags(signals: TurnSignals) -> list[str]:
    thr = POLICY["red_flag_threshold"]
    return [name for name in POLICY["red_flags"] if signals.p(name) >= thr]


def _matches(when: dict, *, score: float, flags: list[str], level: int, pii: int, model_level: int,
             memory: bool = False, granny_hangup: bool = False, failed: bool = False, unanswered: bool = False) -> bool:
    checks = {
        "score_gte": lambda v: score >= v,
        "red_flags_gte": lambda v: len(flags) >= v,
        "any_flag": lambda v: any(f in flags for f in v),
        "level_gte": lambda v: level >= v,
        "pii_attempts_gte": lambda v: pii >= v,
        "model_level_gte": lambda v: model_level >= v,
        "memory_match": lambda v: memory == v,
        "granny_hangup": lambda v: granny_hangup == v,
        "verification_failed": lambda v: failed == v,
        "verification_unanswered": lambda v: unanswered == v,
    }
    return all(checks[k](v) for k, v in when.items())


def allowed_tools(state: PolicyState) -> list[str]:
    if state.call_ended:
        return list(POLICY["post_call_tools"])
    tools = list(POLICY["tools_by_level"][state.level])
    if state.pii_guard_active or state.level >= 3:
        tools += POLICY["pii_guard_tools"]
    return tools


def evaluate(state: PolicyState, signals: TurnSignals, *, fast_path_only: bool = False) -> Decision:
    flags = red_flags(signals)
    inputs = {
        "score": signals.scam_score,
        "red_flags": flags,
        "pii_attempts": state.pii_attempts,
        "model_level": signals.recommended_level,
        "provider": signals.provider,
        "memory_match": state.memory_match,
        "granny_hangup": state.granny_hangup,
    }
    for rule in POLICY["rules"]:
        if _matches(rule["when"], score=signals.scam_score, flags=flags, level=state.level,
                    pii=state.pii_attempts, model_level=signals.recommended_level,
                    memory=state.memory_match, granny_hangup=state.granny_hangup,
                    failed=state.verification_failed, unanswered=state.verification_unanswered):
            break
    target = rule["level"]
    trusted = rule["id"] in ("R3d_granny_asked", "R2d_known_scam_script", "R3f_failed_verification", "R2e_verification_unanswered")
    cap = POLICY["fast_path_max_level_trusted"] if trusted else POLICY["fast_path_max_level"]
    capped = fast_path_only and target > cap
    if capped:
        target = cap
    new_level = max(state.level, target)
    prev = state.level
    after = PolicyState(level=new_level, pii_attempts=state.pii_attempts, pii_guard_active=state.pii_guard_active,
                        memory_match=state.memory_match, granny_hangup=state.granny_hangup)
    if state.verified and new_level > prev and rule["id"] not in POLICY["verification"]["hard_rules"]:
        # The caller proved who they are: only hard rules (coercion, repeat PII, granny's request) can escalate now.
        new_level, capped = prev, True
        rule = {**rule, "description": rule["description"] + " (held: caller passed the identity check)"}
        after.level = prev
    actions = list(POLICY["on_enter"].get(new_level, [])) if new_level > prev else []
    return Decision(
        rule_id=rule["id"],
        description=rule["description"] + (" (capped: fast path alone can't go past caution)" if capped else ""),
        prev_level=prev,
        level=new_level,
        target_level=rule["level"],
        capped=capped,
        inputs=inputs,
        allowed_tools=allowed_tools(after),
        actions=actions,
    )


def needs_verification(state: PolicyState, signals: TurnSignals, claimed: str | None, verifying: bool) -> str | None:
    """Is the guardian uncertain enough to ask an identity question? Returns the reason in plain English, or None."""
    if verifying or state.verified or state.verification_failed or state.level >= 3:
        return None
    cfg = POLICY["verification"]
    score, thr = signals.scam_score, POLICY["red_flag_threshold"]
    if claimed == "a family member":
        f = cfg["family"]
        why = [n.replace("_", " ") for n in f["needs_any"] if signals.p(n) >= thr]
        if why and score < f["score_lt"]:
            return (f"the caller says they're family and {', '.join(why)} (scam likelihood {score:.0%}). "
                    "A real emergency and a grandparent scam sound the same, so I'll ask something only family would know.")
        return None
    i = cfg["institution"]
    flags = red_flags(signals)
    if claimed and i["score_gte"] <= score < i["score_lt"] and len(flags) <= i["max_red_flags"]:
        return (f"the caller says they're {claimed}, but the picture is mixed (scam likelihood {score:.0%}, "
                f"{len(flags)} red flag). A real {claimed} is happy to be called back, so I'll ask that.")
    return None


def required_level(tool: str) -> int | None:
    for lvl in sorted(POLICY["tools_by_level"]):
        if tool in POLICY["tools_by_level"][lvl]:
            return lvl
    return None
