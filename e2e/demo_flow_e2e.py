"""The whole stage demo on the real pages with live audio flowing both ways.

Speech recognition is simulated exactly the way the browser sends it ({"type":"stt",...} on the phone
socket), because Web Speech can't run headless. Everything else is real: audio relay, delay line,
decisions (Jev via TokenRouter), agent, whispers, PII mute, hang-up, report.

    cd backend && uv run --with playwright python ../e2e/demo_flow_e2e.py
"""

import asyncio
import json
import ssl
import sys
import urllib.request

_CTX = ssl._create_unverified_context()


def get(url: str):
    return json.loads(urllib.request.urlopen(url, timeout=10, context=_CTX).read())

from playwright.async_api import async_playwright

from audio_e2e import BASE, HERE, forget_test_scams, phone

SCAM = [
    "Hi, this is Mark from Chase's fraud department, we've seen suspicious charges on your account.",
    "To secure your account, I just need to verify your account number.",
    "Ma'am, don't hang up, and don't tell your family, or your account will be frozen today.",
]


async def stt(page, text: str, partial_words: bool = True):
    words = text.split()
    if partial_words:
        for i in range(1, len(words) + 1):
            await page.evaluate("t => window.__ws.send(JSON.stringify({type:'stt', final:false, text:t}))", " ".join(words[:i]))
            await asyncio.sleep(0.12)
    await page.evaluate("t => window.__ws.send(JSON.stringify({type:'stt', final:true, text:t}))", text)


async def rx(page) -> float:
    """Seconds of audio this page has received so far."""
    return await page.evaluate("window.__rx.reduce((n, f) => n + f.length, 0) / 16000")


async def main() -> int:
    urllib.request.urlopen(urllib.request.Request(BASE + "/api/reset", method="POST"), timeout=10, context=_CTX)
    checks: list[tuple[str, bool, str]] = []
    async with async_playwright() as pw:
        cb, caller, cerr = await phone(pw, HERE / "scammer.wav", "/caller")
        gb, granny, gerr = await phone(pw, HERE / "granny.wav", "/granny")
        panel_b = await pw.chromium.launch(channel="chrome", headless=True)
        panel = await (await panel_b.new_context(viewport={"width": 1280, "height": 720}, ignore_https_errors=True)).new_page()
        await panel.goto(BASE + "/panel")
        await asyncio.sleep(1.5)
        await caller.get_by_label("Call Grandma").click()
        await granny.get_by_label("Answer").click(timeout=8000)
        await asyncio.sleep(3)
        flowing = await rx(caller), await rx(granny)
        checks.append(("audio flows both ways before anything happens", min(flowing) > 1.0, f"seconds rx caller/granny {flowing}"))

        await stt(caller, SCAM[0]); await asyncio.sleep(5)
        before_ask = await rx(caller)
        await stt(granny, "Guardian, is this real?"); await asyncio.sleep(2)
        asked_leak = await rx(caller) - before_ask
        checks.append(("granny's private question to the guardian never reached the caller", asked_leak < 0.6,
                       f"{asked_leak:.2f}s of granny audio reached caller"))
        await asyncio.sleep(4)
        await stt(caller, SCAM[1]); await asyncio.sleep(6)

        urllib.request.urlopen(urllib.request.Request(BASE + "/api/audio/reset", method="POST"), timeout=10, context=_CTX)
        await stt(granny, "Oh my, let me find it... it's four, seven, two, nine, is that right dear?")
        await asyncio.sleep(2.5)
        await caller.screenshot(path=str(HERE / "caller_mid_call.png"))
        await granny.screenshot(path=str(HERE / "granny_mid_call.png"))
        before_after = await rx(caller)
        await asyncio.sleep(2.0)
        resumed = await rx(caller) - before_after
        cut = get(BASE + "/api/audio").get("cut_frames", 0)
        checks.append(("only the number was silenced for the caller (redaction window)", cut > 0, f"{cut} audio chunks cut"))
        checks.append(("granny's voice reaches the caller again right after the number", resumed > 1.0,
                       f"{resumed:.2f}s of granny audio in the 2s after the number"))

        await asyncio.sleep(4)
        await stt(caller, SCAM[2]); await asyncio.sleep(9)
        gm = await granny.evaluate("window.__msgs")
        cm = await caller.evaluate("window.__msgs")
        whispers = [m["text"] for m in gm if m.get("type") == "whisper"]
        checks.append(("granny got a specific warning when asked for her account number",
                       any("never calls for that" in w for w in whispers), ""))
        checks.append(("granny heard 'I kept your account number private'", any("number private" in w for w in whispers), ""))
        checks.append(("granny got warning, private answer, redaction, end and next-step whispers", len(whispers) >= 5,
                       json.dumps(whispers)[:600]))
        checks.append(("every whisper used the natural voice (no robotic fallback)",
                       all(m.get("audio_b64") for m in gm if m.get("type") == "whisper"),
                       f"{sum(1 for m in gm if m.get('type') == 'whisper' and m.get('audio_b64'))}/{len(whispers)} with studio audio"))
        checks.append(("granny's phone vibrated", any(m.get("type") == "vibrate" for m in gm), ""))
        ended = [m for m in cm if m.get("type") == "call_ended"]
        checks.append(("scammer's call was ended by the agent", bool(ended) and ended[0].get("by") == "agent",
                       ended[0].get("message", "") if ended else "no call_ended"))
        checks.append(("scammer never received a whisper", not any(m.get("type") == "whisper" for m in cm), ""))
        await asyncio.sleep(6)
        await panel.screenshot(path=str(HERE / "panel_after_demo.png"))
        shot_c = HERE / "caller_after_demo.png"
        await caller.screenshot(path=str(shot_c))
        await granny.screenshot(path=str(HERE / "granny_after_demo.png"))
        calls = get(BASE + "/api/calls")
        rep = {}
        for _ in range(40):  # the AI-written summary can take a while after the call
            try:
                rep = get(f"{BASE}/api/calls/{calls[0]['call_id']}/report")
                break
            except urllib.error.HTTPError:
                await asyncio.sleep(1)
        checks.append(("incident report written", rep.get("max_level") == 3,
                       f"{rep.get('outcome')} · cost ${rep.get('cost_usd')} · next step: {rep.get('safe_next_step')}"))
        for b in (cb, gb, panel_b):
            await b.close()
    errs = [e for e in cerr + gerr if "vibrate" not in e]
    if errs:
        print("page errors:", errs[:6])
    ok = True
    for name, good, detail in checks:
        print(f"{'PASS' if good else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
        ok &= good
    return 0 if ok else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    forget_test_scams(BASE)
    sys.exit(code)
