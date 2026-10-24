import pytest

from app.hub import NullHub
from app.replay import load_scripts, run_script

SCRIPTS = load_scripts()


async def play(script_id):
    hub = NullHub()
    session = hub.new_session(virtual=True)
    return await run_script(session, SCRIPTS[script_id])


def types(session, t):
    return [e for e in session.events if e["type"] == t]


@pytest.mark.parametrize("script_id", sorted(SCRIPTS))
async def test_script_lands_on_expected_level(script_id):
    s = await play(script_id)
    script = SCRIPTS[script_id]
    assert s.max_level == script["expected_max_level"], [
        (e["data"]["rule_id"], e["data"]["level"]) for e in types(s, "rule_fired")]
    if script["kind"] == "legit":
        assert s.max_level < 3 and s.ended_by != "agent"
    if script.get("expected_outcome") == "terminated":
        assert s.ended_by == "agent"
    assert s.report is not None and s.report["call_id"] == s.id


async def test_stage_demo_walks_the_whole_ladder():
    s = await play("scam_bank")
    levels = [e["data"]["to"] for e in types(s, "level_change")]
    assert levels == [1, 2, 3]
    guard = types(s, "pii_guard")
    assert guard and guard[0]["data"]["action"] == "redacting" and guard[0]["data"]["kind"] == "account"
    assert "caller asked for it" in guard[0]["data"]["reason"]
    blocked = types(s, "blocked_by_policy")
    assert any(b["data"]["tool"] == "terminate_call" and b["data"]["level"] == 2 for b in blocked)
    whispers = [e["data"]["args"]["text"] for e in types(s, "tool_call") if e["data"]["tool"] == "whisper_to_granny"]
    assert any("Chase never calls for that" in w for w in whispers)  # names who they claim to be
    assert any("call Chase at the number on the back of your card" in w for w in whispers)
    assert any("kept your account number private" in w for w in whispers)
    assert any("ended this call" in w for w in whispers)
    rule = [e["data"]["rule_id"] for e in types(s, "rule_fired") if e["data"]["level"] == 3]
    assert rule and rule[0] == "R3b_alert_plus_coercion"


async def test_pii_digits_are_redacted_and_the_call_goes_on():
    s = await play("scam_bank")
    guard = [e["data"] for e in types(s, "pii_guard")]
    assert [g["action"] for g in guard][:2] == ["redacting", "redacted"]
    assert guard[1]["digits_hidden"] == 4
