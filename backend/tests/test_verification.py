from app.hub import NullHub
from app.policy.engine import PolicyState, needs_verification
from app.signals.model import Signal, TurnSignals


def sigs(score, **ps):
    return TurnSignals(provider="t", scam_score=score, recommended_level=1,
                       signals={k: Signal(p=v, value=v >= 0.6) for k, v in ps.items()})


def test_uncertain_family_money_call_triggers_a_check():
    why = needs_verification(PolicyState(level=1), sigs(0.7, requests_unusual_payment=0.9), "a family member", False)
    assert why and "family" in why


def test_certain_scam_skips_the_check():
    assert needs_verification(PolicyState(level=2), sigs(0.99, requests_unusual_payment=0.9), "a family member", False) is None


def test_normal_family_chat_needs_no_check():
    assert needs_verification(PolicyState(), sigs(0.1), "a family member", False) is None


def test_mixed_bank_call_gets_the_callback_question():
    why = needs_verification(PolicyState(level=1), sigs(0.6, claims_authority=0.9), "Chase", False)
    assert why and "called back" in why


async def _family_call(answer: str):
    s = NullHub().new_session(virtual=True)
    await s.dial()
    await s.answer()
    await s.say("caller", "Grandma, it's me, Jake. I got in a car accident and I need four hundred dollars for the tow truck right now.")
    await s.drain(include_finalize=False)
    await s.say("caller", answer)
    await s.drain(include_finalize=False)
    if s.state == "active":
        await s.hangup("caller")
    await s.drain()
    return s


def stages(s):
    return [e["data"]["stage"] for e in s.events if e["type"] == "verification"]


async def test_real_grandson_passes_and_is_trusted():
    s = await _family_call("It's Biscuit, Grandma! Biscuit the beagle.")
    assert stages(s)[:2] == ["started", "asked"] and "passed" in stages(s)
    assert s.policy.verified and s.ended_by != "agent" and s.max_level <= 1
    assert s.report["verification"]["result"] == "passed"


async def test_fake_grandson_fails_and_gets_hung_up_on():
    s = await _family_call("Grandma please, I don't remember, there's no time, I just need the money.")
    assert "failed" in stages(s)
    assert s.ended_by == "agent"
    rule = [e["data"]["rule_id"] for e in s.events if e["type"] == "rule_fired" and e["data"]["level"] == 3]
    assert rule == ["R3f_failed_verification"]
