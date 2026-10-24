from app import store
from app.config import settings
from app.hub import NullHub
from app.replay import load_scripts, run_script


async def live_session():
    session = NullHub().new_session(virtual=True)
    await session.dial()
    await session.answer()
    return session


def events(s, t):
    return [e["data"] for e in s.events if e["type"] == t]


async def test_granny_asks_is_this_real_privately():
    s = await live_session()
    await s.say("caller", "Hi, this is Mark from Chase's fraud department, we've seen suspicious charges on your account.")
    await s.drain(include_finalize=False)
    await s.say("granny", "Guardian, is this real?")
    await s.drain(include_finalize=False)
    ask = events(s, "guardian_ask")
    assert ask and ask[0]["action"] == "answered" and "Chase" in ask[0]["answer"]
    assert s.muted  # the caller never heard the question
    assert s.policy.pii_attempts == 0
    whispers = [e["args"]["text"] for e in events(s, "tool_call") if e["tool"] == "whisper_to_granny"]
    assert ask[0]["answer"] in whispers


async def test_granny_asks_guardian_to_hang_up_on_a_normal_call():
    s = await live_session()
    await s.say("caller", "Hi Grandma, are we still on for dinner Sunday?")
    await s.drain(include_finalize=False)
    await s.say("granny", "Guardian, hang up for me.")
    await s.drain()
    assert s.ended_by == "agent" and s.outcome == "ended at granny's request"
    rules = [e["rule_id"] for e in events(s, "rule_fired") if e["level"] == 3]
    assert rules == ["R3d_granny_asked"]
    whispers = [e["args"]["text"] for e in events(s, "tool_call") if e["tool"] == "whisper_to_granny"]
    assert any("You did the right thing" in w for w in whispers)
    assert not any("It was a scam" in w for w in whispers)  # never tell her a normal call was a scam
    assert s.report["safe_next_step"] is None


async def test_scam_memory_recognises_a_repeat_script(monkeypatch):
    monkeypatch.setattr(settings, "memory_include_virtual", True)
    store.forget_scams()
    script = load_scripts()["scam_bank"]
    first = await run_script(NullHub().new_session(virtual=True), script)
    assert first.scam_confirmed and store.scam_memory()
    second = await run_script(NullHub().new_session(virtual=True), script)
    match = events(second, "memory_match")
    assert match and match[0]["past_call_id"] == first.id and match[0]["similarity"] > 0.9
    first_rise = next(e for e in events(second, "rule_fired") if e["level"] > e["prev_level"])
    assert first_rise["rule_id"] == "R2d_known_scam_script" and first_rise["level"] == 2 and first_rise["turn"] == 1
    store.forget_scams()


async def test_whisper_echo_is_ignored_but_granny_is_not():
    s = await live_session()
    s.virtual = False  # ducking only applies to real calls
    await s.whisper("Be careful, this might not be who they say.", "caution")
    assert s.is_whisper_echo("be careful this might not be")
    assert not s.is_whisper_echo("Oh my, let me find it, it's four seven two")
    s.virtual = True


async def test_specific_alert_follows_a_generic_one():
    """If the model jumps to Alert before the ask, granny still hears exactly what they want once they ask."""
    from app.signals.model import Signal, TurnSignals

    s = await live_session()
    s.claimed_org = "Chase"
    def sig(**ps):
        return TurnSignals(provider="t", scam_score=0.85, recommended_level=2,
                           signals={k: Signal(p=v, value=v >= 0.6) for k, v in ps.items()})
    from app.policy.engine import evaluate
    s.last_signals = sig(claims_authority=0.9, creates_urgency=0.9)
    await s._apply(evaluate(s.policy, s.last_signals), 1)
    s.last_signals = sig(claims_authority=0.9, requests_credentials=0.95)
    await s._apply(evaluate(s.policy, s.last_signals), 2)
    await s._apply(evaluate(s.policy, s.last_signals), 3)  # no repeat
    whispers = [e["args"]["text"] for e in events(s, "tool_call") if e["tool"] == "whisper_to_granny"]
    assert sum("Chase never calls for that" in w for w in whispers) == 1
    assert any("This sounds like a scam" in w for w in whispers)
