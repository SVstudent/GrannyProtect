"""Trusted-person alerts: a Telegram bot messages a loved one while the scam is happening, and again after.

Setup (2 minutes): create a bot with @BotFather, put TELEGRAM_BOT_TOKEN in .env, then have the loved one send
the bot any message (e.g. "hi"). The chat id is discovered automatically from that message, or set
TELEGRAM_CHAT_ID yourself.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

import httpx

from .config import settings

if TYPE_CHECKING:
    from .call import CallSession

API = "https://api.telegram.org/bot{token}/{method}"
_chat_id_cache: str | None = None


def _chat_id_file():
    return settings.cache_dir / "telegram_chat_id"


async def _tg(method: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(API.format(token=settings.telegram_bot_token, method=method), json=params)
        return r.json()


async def chat_id() -> str | None:
    """The loved one's chat: from .env, from cache, or from the latest message anyone sent the bot."""
    global _chat_id_cache
    if settings.telegram_chat_id:
        return settings.telegram_chat_id
    if _chat_id_cache:
        return _chat_id_cache
    f = _chat_id_file()
    if f.exists():
        _chat_id_cache = f.read_text().strip()
        return _chat_id_cache
    data = await _tg("getUpdates", limit=20)
    chats = [u.get("message", {}).get("chat", {}).get("id") for u in data.get("result", []) if u.get("message")]
    if chats and chats[-1]:
        _chat_id_cache = str(chats[-1])
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        f.write_text(_chat_id_cache)
    return _chat_id_cache


def _wants(session: CallSession) -> str:
    p = session.peak
    asks = []
    if p.get("requests_credentials", 0) >= 0.6:
        asks.append("private details (like an account or card number)")
    if p.get("requests_unusual_payment", 0) >= 0.6:
        asks.append("money by gift card, wire or crypto")
    if p.get("demands_secrecy", 0) >= 0.6:
        asks.append("to keep the call secret from family")
    if p.get("threatens", 0) >= 0.6:
        asks.append("threats (frozen account, arrest)")
    return "; ".join(asks) or "unclear yet"


def build_message(session: CallSession, stage: str) -> str:
    name = html.escape(settings.protected_name, quote=False)
    claims = html.escape(session.claimed_org or "unknown", quote=False)
    score = f"{session.peak.get('scam_score', 0):.0%}"
    hidden = session.policy.pii_attempts
    if stage == "alert":
        protected = f"{name} has been warned privately"
        lines = [
            f"⚠️ <b>{name} may be getting scammed right now</b>",
            "",
            f"• Caller claims to be: <b>{claims}</b>",
            f"• They want: {html.escape(_wants(session), quote=False)}",
            f"• Scam likelihood: <b>{score}</b>",
            "",
            f"Granny-Protect is on the call. {protected}"
            + (f", and the number {name} started reading was kept from the caller." if hidden else "."),
            f"Please call {name} as soon as the call ends. Don't call back the number that called.",
        ]
        if settings.public_url:
            lines += ["", f'👉 <a href="{html.escape(settings.public_url.rstrip("/"))}/report/{session.id}">See what\'s happening (live)</a>']
        return "\n".join(lines)
    got = ("nothing: the number was hidden from the caller" if hidden else "nothing") if session.scam_confirmed else "n/a"
    lines = [
        f"✅ <b>Scam call stopped</b> · {name} is safe",
        "",
        f"Granny-Protect hung up on a caller pretending to be <b>{claims}</b>.",
        f"• They wanted: {html.escape(_wants(session), quote=False)}",
        f"• They got: {html.escape(got, quote=False)}",
    ]
    if session.report and session.report.get("safe_next_step"):
        lines += ["", f"<b>Next step:</b> {html.escape(session.report['safe_next_step'], quote=False)}"]
    elif session.safe_step:
        lines += ["", f"<b>Next step:</b> {html.escape(session.safe_step, quote=False)}."]
    if settings.public_url:
        lines += ["", f'<a href="{html.escape(settings.public_url.rstrip("/"))}/report/{session.id}">Full incident report</a>']
    lines += ["", f"{name} did everything right. A quick call would mean a lot."]
    return "\n".join(lines)


async def send(session: CallSession, stage: str) -> dict:
    """Send the alert on every configured channel. Returns what happened, for the decision log."""
    text = build_message(session, stage)
    out: dict = {"stage": stage, "preview": text}
    sent_any = False
    if settings.telegram_bot_token:
        try:
            cid = await chat_id()
            if not cid:
                out["telegram"] = "no chat yet: the loved one must send the bot a message once"
            else:
                r = await _tg("sendMessage", chat_id=cid, text=text, parse_mode="HTML", disable_web_page_preview=True)
                out["telegram"] = "sent" if r.get("ok") else f"error: {r.get('description')}"
                sent_any |= bool(r.get("ok"))
        except Exception as e:  # noqa: BLE001
            out["telegram"] = f"error: {e!r}"
    if settings.discord_webhook_url:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(settings.discord_webhook_url, json={"content": text.replace("<b>", "**").replace("</b>", "**")})
            out["discord"] = "sent" if r.status_code < 300 else f"error {r.status_code}"
            sent_any |= r.status_code < 300
        except Exception as e:  # noqa: BLE001
            out["discord"] = f"error: {e!r}"
    if not settings.telegram_bot_token and not settings.discord_webhook_url:
        out["channel"] = "log-only (set TELEGRAM_BOT_TOKEN to message a loved one)"
    out["sent"] = sent_any
    return out


async def status() -> dict:
    if not settings.telegram_bot_token:
        return {"configured": False, "hint": "Create a bot with @BotFather and set TELEGRAM_BOT_TOKEN in .env"}
    me = await _tg("getMe")
    cid = await chat_id()
    return {"configured": True, "bot": (me.get("result") or {}).get("username"), "ok": me.get("ok"),
            "chat_id": cid, "hint": None if cid else "Have the loved one open the bot and send it any message"}
