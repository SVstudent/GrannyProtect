import time

from app.hub import NullHub


class CaptureHub(NullHub):
    def __init__(self):
        super().__init__()
        self.sent = {"caller": [], "granny": []}

    async def send_json(self, role, msg):
        self.sent[role].append(msg)


async def risky_call():
    hub = CaptureHub()
    s = hub.new_session()  # a real (non-virtual) call: phones get messages
    await s.dial()
    await s.answer()
    s.policy.level = 1
    s.credential_requested = True
    s.credential_ask = "To secure your account, I just need to verify your account number."
    return hub, s


def guard(s):
    return [e["data"] for e in s.events if e["type"] == "pii_guard"]


async def test_only_the_digits_are_cut_and_the_call_continues():
    hub, s = await risky_call()
    t_before = time.monotonic()
    words = "Oh my, let me find it, it's four seven two nine, is that right dear?".split()
    for i in range(1, len(words) + 1):
        await s.on_partial("granny", " ".join(words[:i]))
    g = guard(s)
    assert [x["action"] for x in g] == ["redacting", "redacted"]
    assert g[0]["kind"] == "account" and g[1]["digits_hidden"] == 4
    assert not s.muted and s.redaction is None  # her voice is back for "is that right dear?"
    (start, end), = s.cuts
    assert end is not None and start < end
    assert not s.in_cut(t_before - 1.0)        # "Oh my, let me find it" (spoken before) still reaches the caller
    assert not s.in_cut(time.monotonic() + 0.1)  # what she says after the number reaches the caller
    whispers = [m["text"] for m in hub.sent["granny"] if m["type"] == "whisper"]
    assert "I kept your account number private." in whispers
    s.close()


async def test_card_numbers_are_recognised():
    _, s = await risky_call()
    s.credential_ask = "Read me the number on your credit card."
    await s.on_partial("granny", "it's four one one one")
    assert guard(s)[0]["kind"] == "card"
    s.close()


async def test_ordinary_call_numbers_flow_freely():
    hub = CaptureHub()
    s = hub.new_session()
    await s.dial()
    await s.answer()
    await s.on_partial("granny", "my number is five five five one two one two")
    assert guard(s) == [] and s.cuts == [] and not s.protecting()
    s.close()


async def test_second_number_attempt_still_escalates():
    _, s = await risky_call()
    await s.on_partial("granny", "it's four seven two nine")
    await s.on_final("granny", "it's four seven two nine", source="browser")
    await s.on_partial("granny", "four seven two nine one")
    assert s.policy.pii_attempts == 2
    s.close()


async def test_subtitles_hide_digits_and_private_questions_from_the_caller():
    hub, s = await risky_call()
    await s.on_final("caller", "I need your account number", source="browser")
    await s.on_final("granny", "Sure, it's four seven two nine, okay?", source="browser")
    await s.on_partial("granny", "Guardian, is this real?")
    caller_subs = [m for m in hub.sent["caller"] if m["type"] == "subtitle"]
    granny_subs = [m for m in hub.sent["granny"] if m["type"] == "subtitle"]
    granny_lines = [m["text"] for m in caller_subs if m["speaker"] == "granny"]
    assert granny_lines == ["Sure, it's ••••, okay?"] and caller_subs[-1]["redacted"]
    assert not any("Guardian" in m["text"] for m in caller_subs)          # private question never reaches the caller
    assert any(m["private"] and "Guardian" in m["text"] for m in granny_subs)
    assert any(m["speaker"] == "caller" for m in granny_subs)             # granny sees what the caller says
    assert not any(m["speaker"] == "guardian" for m in caller_subs)       # whispers never reach the caller
    s.close()


async def test_no_protection_delay_on_an_ordinary_call():
    hub = CaptureHub()
    s = hub.new_session()
    await s.dial()
    await s.answer()
    assert not s.protecting()
    s.policy.level = 1
    assert s.protecting()
    s.close()
