import pytest

from app.agent.tools import run_tool
from app.hub import NullHub
from app.supervisor import Supervisor, sup


@pytest.fixture
def fresh(monkeypatch):
    s = Supervisor()
    for mod in ("app.supervisor", "app.agent.tools", "app.agent.runner", "app.call"):
        monkeypatch.setattr(f"{mod}.sup", s, raising=False)
    return s


async def active_session(s):
    hub = NullHub()
    s.hub = hub
    session = hub.new_session(virtual=True)
    await session.dial()
    await session.answer()
    return session


async def test_budget_drops_to_economy_then_rules_only(fresh):
    session = await active_session(fresh)
    await fresh.set_budget(call_usd=1.0, shift_usd=0.01)
    await fresh.record("agent", 0.0085, 900, session.id)
    assert fresh.mode == "economy" and not fresh.allow("agent") and fresh.allow("jev")
    await fresh.record("jev", 0.002, 1100, session.id)
    assert fresh.mode == "rules-only" and not fresh.allow("jev")
    modes = [e["data"]["message"] for e in session.events if e["type"] == "supervisor" and e["data"]["kind"] == "mode"]
    assert len(modes) == 2


async def test_per_call_budget(fresh):
    session = await active_session(fresh)
    await fresh.set_budget(call_usd=0.001, shift_usd=5)
    await fresh.record("agent", 0.0012, 500, session.id)
    assert fresh.mode == "economy" and "this call" in fresh.mode_reason


async def test_circuit_breaker_opens_and_recovers(fresh):
    session = await active_session(fresh)
    await fresh.failure("jev", TimeoutError(), "llm")
    assert fresh.allow("jev")
    await fresh.failure("jev", TimeoutError(), "llm")
    assert fresh.health["jev"].state == "open" and not fresh.allow("jev")
    fresh.health["jev"].opened_at -= fresh.RETRY_AFTER_S + 1   # half-open window passes
    assert fresh.allow("jev")
    await fresh.record("jev", 0.0, 1000, session.id)
    assert fresh.health["jev"].state == "ok" and fresh.recoveries == 1


async def test_duplicate_agent_tool_call_is_blocked(fresh):
    session = await active_session(fresh)
    session.policy.level = 1
    ok = await run_tool(session, "whisper_to_granny", {"text": "Be careful."}, actor="agent")
    dup = await run_tool(session, "whisper_to_granny", {"text": "be careful. "}, actor="agent")
    assert ok["ok"] and not dup["ok"] and "blocked_by_supervisor" in dup["error"]
    assert fresh.loops_caught == 1


async def test_agent_retrying_refused_tool_gets_suspended(fresh):
    session = await active_session(fresh)
    session.policy.level = 2
    await run_tool(session, "terminate_call", {"reason": "a"}, actor="agent")
    await run_tool(session, "terminate_call", {"reason": "b"}, actor="agent")
    assert session.agent_suspended_level == 2
    r = await run_tool(session, "log_event", {"event": "x"}, actor="agent")
    assert not r["ok"] and "suspended" in r["error"]
    session.policy.level = 3      # escalation lifts the suspension
    r = await run_tool(session, "log_event", {"event": "y"}, actor="agent")
    assert r["ok"]


async def test_policy_actions_are_never_loop_blocked(fresh):
    session = await active_session(fresh)
    session.policy.level = 1
    for _ in range(3):
        r = await run_tool(session, "whisper_to_granny", {"text": "same"}, actor="policy")
        assert r["ok"]


def test_global_supervisor_untouched_by_fixture():
    assert sup.mode in ("full", "economy", "rules-only")


async def test_rules_only_mode_still_stops_the_scam(fresh):
    """Out of budget: no model spend at all, but regex + policy still walk the whole ladder."""
    from app.replay import load_scripts, run_script

    hub = NullHub()
    fresh.hub = hub
    fresh.spend, fresh.shift_budget = 1.0, 0.5
    await fresh._update_mode(None)
    assert fresh.mode == "rules-only"
    session = hub.new_session(virtual=True)
    await run_script(session, load_scripts()["scam_bank"])
    assert session.max_level == 3 and session.ended_by == "agent"
    assert session.report["cost_usd"] == 0.0
