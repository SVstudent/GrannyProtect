"""Pure-voice end to end: the scammer's phone SPEAKS a scam call into its microphone and nothing else
is simulated. Headless Chrome has no working browser speech recognition, so this also proves the
self-healing path: the watchdog notices words aren't arriving, switches that phone to server-side
transcription (TokenRouter), and the guardian still escalates and hangs up from the audio alone.

    cd backend && PYTHONPATH=../e2e uv run --with playwright python ../e2e/voice_only_e2e.py
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

TIMEOUT_S = 150


async def main() -> int:
    urllib.request.urlopen(urllib.request.Request(BASE + "/api/reset", method="POST"), timeout=10, context=_CTX)
    async with async_playwright() as pw:
        cb, caller, _ = await phone(pw, HERE / "scam_call.wav", "/caller")
        gb, granny, _ = await phone(pw, HERE / "silence.wav", "/granny")
        await asyncio.sleep(1.5)
        await caller.get_by_label("Call Grandma").click()
        await granny.get_by_label("Answer").click(timeout=8000)
        t0 = asyncio.get_event_loop().time()
        ended = None
        while asyncio.get_event_loop().time() - t0 < TIMEOUT_S:
            await asyncio.sleep(3)
            cm = await caller.evaluate("window.__msgs")
            ended = next((m for m in cm if m.get("type") == "call_ended"), None)
            if ended:
                break
        gm = await granny.evaluate("window.__msgs")
        await asyncio.sleep(8)
        for b in (cb, gb):
            await b.close()
    calls = get(BASE + "/api/calls")
    rep = {}
    for _ in range(40):
        try:
            rep = get(f"{BASE}/api/calls/{calls[0]['call_id']}/report")
            break
        except urllib.error.HTTPError:
            await asyncio.sleep(1)
    whispers = [m["text"] for m in gm if m.get("type") == "whisper"]
    print("transcript the guardian heard:")
    for line in rep["transcript"]:
        print(f"  {line['speaker']}: {line['text']}")
    print("supervisor:", [s["message"][:90] for s in rep.get("supervisor", [])])
    print("whispers:", whispers)
    checks = [
        ("words reached the guardian (browser speech, or the watchdog's server-transcription fallback)",
         any("transcribing on the server" in s["message"] for s in rep.get("supervisor", []))
         or any(l.get("text") for l in rep["transcript"])),
        ("guardian heard the scammer's words from audio alone", any("account" in l["text"].lower() for l in rep["transcript"])),
        ("granny was warned", len(whispers) >= 1),
        ("guardian escalated", rep["max_level"] >= 2),
        ("call ended by the guardian", bool(ended) and ended.get("by") == "agent"),
    ]
    ok = True
    for name, good in checks:
        print(f"{'PASS' if good else 'FAIL'}  {name}")
        ok &= good
    print(f"outcome: {rep['outcome']} · max level {rep['max_level']} · cost ${rep['cost_usd']}")
    return 0 if ok else 1


if __name__ == "__main__":
    code = asyncio.run(main())
    forget_test_scams(BASE)
    sys.exit(code)
