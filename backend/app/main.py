"""FastAPI app: phone and panel websockets, REST, and the built frontend on one origin."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import autopilot, store
from .config import ROOT, settings
from .hub import Hub
from .replay import load_scripts, run_script, script_index
from .supervisor import sup
from .tts import whispers

hub = Hub()
sup.hub = hub
DIST = ROOT / "frontend" / "dist"

try:  # optional: the same gated tools over MCP at /mcp
    from .mcp_server import build as build_mcp

    mcp_app = build_mcp(hub).http_app(path="/")
except ImportError:
    mcp_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.conn()
    task = asyncio.create_task(whispers.pregenerate())
    sup.start()
    print(f"[granny-protect] STT={settings.stt_provider} signals={settings.signals_provider} "
          f"agent={settings.agent_provider} TTS={settings.tts_provider} key={'yes' if settings.llm_api_key else 'no'} "
          f"mcp={'on' if mcp_app else 'off'}", flush=True)
    if mcp_app is not None:
        async with mcp_app.lifespan(app):
            yield
    else:
        yield
    task.cancel()


app = FastAPI(title="Granny-Protect-5000", lifespan=lifespan)
if mcp_app is not None:
    app.mount("/mcp", mcp_app)


def _current():
    """The live call, replaced by a fresh one once the last call has ended."""
    if hub.session.state == "ended" or hub.session.virtual:
        hub.new_session()
    return hub.session


@app.websocket("/ws/{role}")
async def phone_ws(ws: WebSocket, role: str):
    if role == "panel":
        return await panel_ws(ws)
    if role not in ("caller", "granny"):
        await ws.close(code=4404)
        return
    await ws.accept()
    old = hub.phones.get(role)
    hub.phones[role] = ws
    if old is not None:
        try:
            await old.close(code=4000)
        except Exception:  # noqa: BLE001
            pass
    await ws.send_json(hub.session.state_msg())
    # Control and transcript messages are handled in order by their own task, so slow work (decisions, panel
    # broadcasts, whispers) never holds up this phone's audio frames, which are relayed straight from this loop.
    texts: asyncio.Queue[str | None] = asyncio.Queue()
    worker = asyncio.create_task(_text_worker(role, texts))
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if (data := msg.get("bytes")) is not None:
                hub.audio["in"][role] += 1
                await hub.session.on_audio(role, data)
                continue
            if (text := msg.get("text")) is None:
                continue
            texts.put_nowait(text)
    except WebSocketDisconnect:
        pass
    finally:
        texts.put_nowait(None)  # finish what was already received (e.g. a hangup), then stop
        if hub.phones.get(role) is ws:
            hub.phones[role] = None


async def _text_worker(role: str, texts: "asyncio.Queue[str | None]") -> None:
    while (text := await texts.get()) is not None:
        try:
            await _handle(role, text)
        except Exception as e:  # noqa: BLE001 - one bad message must not stop the phone's control channel
            print(f"[ws/{role}] message error: {e!r}")


async def _handle(role: str, text: str) -> None:
    import json

    try:
        m = json.loads(text)
    except json.JSONDecodeError:
        return
    kind = m.get("type")
    s = hub.session
    if kind == "dial" and role == "caller":
        s = _current()
        await s.dial()
    elif kind == "answer" and role == "granny":
        if s.state == "ended" or s.virtual:
            s = _current()
        await s.answer()
    elif kind == "hangup":
        await s.hangup(role)
    elif kind == "stt":
        s.browser_stt_alive(role)
        if m.get("final"):
            await s.on_final(role, m.get("text", ""), source="browser")
        else:
            await s.on_partial(role, m.get("text", ""))
    elif kind == "say":
        s.spawn(s.say(role, m.get("text", "")))
    elif kind == "whisper_done":
        s.whisper_done(m.get("id", ""))


async def panel_ws(ws: WebSocket):
    await ws.accept()
    for ev in list(hub.session.events):
        await ws.send_json(ev)
    hub.panels.add(ws)
    try:
        while True:
            await ws.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.panels.discard(ws)


@app.get("/api/config")
def config():
    return {
        "stt_provider": settings.stt_provider,
        "signals_provider": settings.signals_provider,
        "agent_provider": settings.agent_provider,
        "tts_provider": settings.tts_provider,
        "challenge_mode": hub.session.challenge_mode,
        "pii_delay_ms": settings.pii_delay_ms,
        "models": {"agent": settings.agent_model, "signals": settings.jev_model if settings.signals_provider == "jev"
                   else settings.signals_llm_model, "stt": settings.stt_model, "tts": settings.tts_model},
        "studio_whispers": len([t for t in whispers.LINES.values() if whispers.cached_audio(t)]),
    }


class Toggle(BaseModel):
    enabled: bool


@app.post("/api/challenge")
async def challenge(t: Toggle):
    settings.challenge_mode = t.enabled
    hub.session.challenge_mode = t.enabled
    await hub.session.emit("info", message=f"Challenge mode {'on' if t.enabled else 'off'}")
    return {"challenge_mode": t.enabled}


@app.get("/api/scripts")
def scripts():
    return script_index()


@app.post("/api/replay/{script_id}")
async def replay(script_id: str):
    scripts = load_scripts()
    if script_id not in scripts:
        raise HTTPException(404, "unknown script")
    if hub.session.state in ("active", "ringing") and not hub.session.virtual:
        raise HTTPException(409, "a live call is in progress")
    session = hub.new_session(virtual=True)
    session.spawn(run_script(session, scripts[script_id], word_delay=0.15, turn_gap=1.2))
    return {"call_id": session.id}


@app.post("/api/reset")
async def reset():
    s = hub.session
    if s.state == "active":
        await s.hangup("caller")
    hub.new_session()
    await hub.broadcast({"type": "call_state", "seq": 0, "ts": 0, "call_id": hub.session.id, "data": {"state": "idle"}})
    await hub.send_json("caller", hub.session.state_msg())
    await hub.send_json("granny", hub.session.state_msg())
    return {"call_id": hub.session.id}


@app.get("/api/audio")
def audio_health():
    """Frame counters for diagnosing a live call (in from each phone, out to each phone)."""
    return {**hub.audio, "phones": {r: ws is not None for r, ws in hub.phones.items()}, "call": hub.session.state}


@app.post("/api/audio/reset")
def audio_reset():
    hub.audio.update(**{"in": {"caller": 0, "granny": 0}, "out": {"caller": 0, "granny": 0}, "send_errors": 0,
                        "send_ms_max": 0.0, "cut_frames": 0})
    return hub.audio


@app.get("/api/telegram")
async def telegram_status():
    from . import notify

    return await notify.status()


@app.post("/api/telegram/test")
async def telegram_test():
    """Send a sample alert so you can check the loved one's phone buzzes."""
    from . import notify

    s = hub.session
    if not settings.telegram_bot_token:
        raise HTTPException(400, "Set TELEGRAM_BOT_TOKEN in .env first")
    cid = await notify.chat_id()
    if not cid:
        raise HTTPException(400, "Have the loved one send the bot any message first")
    text = (f"👋 This is Granny-Protect-5000. You're now {settings.protected_name}'s trusted contact. "
            f"If a scam call reaches {settings.protected_name}, you'll get a message here right away.")
    r = await notify._tg("sendMessage", chat_id=cid, text=text)
    return {"ok": r.get("ok"), "chat_id": cid, "call": s.id}


@app.get("/api/memory")
def memory_list():
    return store.scam_memory()


@app.delete("/api/memory")
def memory_clear():
    """Forget remembered scam scripts (e.g. before a demo, so the first call climbs the whole ladder)."""
    return {"forgotten": store.forget_scams()}


@app.get("/api/shift")
def shift():
    return sup.heartbeat()


class Budget(BaseModel):
    call_usd: float | None = None
    shift_usd: float | None = None


@app.post("/api/budget")
async def budget(b: Budget):
    await sup.set_budget(b.call_usd, b.shift_usd)
    return sup.heartbeat()


class Autopilot(BaseModel):
    minutes: float = 120


@app.post("/api/autopilot")
async def autopilot_start(a: Autopilot):
    autopilot.start(hub, a.minutes)
    return sup.autopilot


@app.post("/api/autopilot/stop")
async def autopilot_stop():
    await autopilot.stop()
    return sup.autopilot


@app.get("/api/calls")
def calls():
    return store.list_calls()


@app.get("/api/calls/{call_id}/report")
def report(call_id: str):
    r = store.get_report(call_id)
    if r is None:
        s = hub.session
        if s.id == call_id and s.state in ("active", "ended"):
            return s.live_report()  # call still going (or its report is being written): live view
        raise HTTPException(404, "report not ready")
    return r


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        if path and f.is_file() and DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
