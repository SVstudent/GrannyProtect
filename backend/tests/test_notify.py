from app import notify
from app.config import settings
from app.hub import NullHub
from app.replay import load_scripts, run_script


async def test_loved_one_gets_an_alert_during_and_a_summary_after(monkeypatch):
    sent = []

    async def fake_tg(method, **params):
        if method == "sendMessage":
            sent.append(params)
        return {"ok": True, "result": []}

    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "12345")
    monkeypatch.setattr(settings, "protected_name", "Grandma Rose")
    monkeypatch.setattr(notify, "_tg", fake_tg)

    s = await run_script(NullHub().new_session(virtual=True), load_scripts()["scam_bank"])
    assert sent == []  # practice (virtual) calls never message a real loved one

    s = NullHub().new_session()  # the same scam as a real call
    s.virtual = False
    await run_script(s, load_scripts()["scam_bank"])

    assert len(sent) == 2, [m["text"][:60] for m in sent]
    alert, done = sent[0]["text"], sent[1]["text"]
    assert sent[0]["chat_id"] == "12345" and sent[0]["parse_mode"] == "HTML"
    assert "Grandma Rose may be getting scammed right now" in alert
    assert "Chase" in alert and "account or card number" in alert
    assert "Scam call stopped" in done and "Chase" in done and "Next step" in done
    tool_results = [e["data"] for e in s.events if e["type"] == "tool_result" and e["data"]["tool"] == "notify_family"]
    assert [r["result"]["telegram"] for r in tool_results if "telegram" in r["result"]] == ["sent", "sent"]


async def test_no_summary_message_for_a_normal_call(monkeypatch):
    sent = []

    async def fake_tg(method, **params):
        sent.append(params)
        return {"ok": True}

    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_chat_id", "12345")
    monkeypatch.setattr(notify, "_tg", fake_tg)
    await run_script(NullHub().new_session(virtual=True), load_scripts()["legit_grandson"])
    assert sent == []
