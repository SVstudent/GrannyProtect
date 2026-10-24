"""End-to-end voice test: two real Chrome instances run the real /caller and /granny pages, each with a
fake microphone playing a spoken WAV. We capture the audio each phone RECEIVES, then transcribe it
with TokenRouter to prove speech crossed the call intact in both directions.

    cd backend && uv run --with playwright python ../e2e/audio_e2e.py [base_url]

The backend must be running (default http://localhost:8000). Uses your installed Google Chrome.
"""

import asyncio
import base64
import json
import sys
import time
import wave
from pathlib import Path

from playwright.async_api import async_playwright

HERE = Path(__file__).parent
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
LISTEN_S = 14
import os
NO_SPEECH = os.getenv("NO_SPEECH") == "1"

# Records every binary frame the page receives over the phone socket, and counts frames sent and
# audio buffers actually scheduled for playback.
INIT = """
(() => {
  window.__rx = []; window.__tx = 0; window.__txPeak = 0; window.__played = 0;
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) {
    if (this.url.includes('/ws/')) window.__ws = this;
    if (typeof d !== 'string') {
      window.__tx++; (window.__txt = window.__txt || []).push(performance.now());
      const a = new Int16Array(d);
      for (let i = 0; i < a.length; i += 4) window.__txPeak = Math.max(window.__txPeak, Math.abs(a[i]));
    }
    return send.call(this, d);
  };
  const add = WebSocket.prototype.addEventListener;
  const desc = Object.getOwnPropertyDescriptor(WebSocket.prototype, 'onmessage');
  window.__msgs = [];
  const tap = (ev) => {
    if (ev.data instanceof ArrayBuffer) { window.__rx.push(Array.from(new Int16Array(ev.data.slice(0)))); (window.__rxt = window.__rxt || []).push(performance.now()); }
    else if (typeof ev.data === 'string') { try { window.__msgs.push(JSON.parse(ev.data)); } catch (e) {} }
  };
  Object.defineProperty(WebSocket.prototype, 'onmessage', {
    set(fn) { desc.set.call(this, fn && ((ev) => { tap(ev); return fn.call(this, ev); })); },
    get() { return desc.get.call(this); },
  });
  WebSocket.prototype.addEventListener = function (type, fn, ...rest) {
    if (type === 'message') { const orig = fn; fn = function (ev) { tap(ev); return orig.call(this, ev); }; }
    return add.call(this, type, fn, ...rest);
  };
  const start = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (...a) { window.__played++; return start.apply(this, a); };
  // Fake microphone: this phone's own voice file, looped, as a real MediaStream.
  window.__gum = 0;
  navigator.mediaDevices.getUserMedia = async () => {
    window.__gum++;
    const ctx = new AudioContext();
    const data = await (await fetch('/__e2e/voice.wav')).arrayBuffer();
    const buf = await ctx.decodeAudioData(data);
    const src = ctx.createBufferSource();
    src.buffer = buf; src.loop = true;
    const dst = ctx.createMediaStreamDestination();
    src.connect(dst); src.start();
    return dst.stream;
  };
})();
"""


def forget_test_scams(base: str) -> None:
    """E2E calls are real calls, so the guardian memorises their scripts; wipe that so the stage demo starts fresh."""
    import ssl
    import urllib.request

    urllib.request.urlopen(urllib.request.Request(base + "/api/memory", method="DELETE"), timeout=10,
                           context=ssl._create_unverified_context())


def save_wav(path: Path, frames: list[list[int]]) -> float:
    import array

    pcm = array.array("h", [s for f in frames for s in f])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    if not pcm:
        return 0.0
    return (sum(x * x for x in pcm[::4]) / len(pcm[::4])) ** 0.5


async def phone(pw, wav: Path, path: str):
    browser = await pw.chromium.launch(
        channel="chrome",
        headless=True,
        args=[
            "--use-fake-device-for-media-stream",
            "--use-fake-ui-for-media-stream",
            f"--use-file-for-fake-audio-capture={wav}",
            "--autoplay-policy=no-user-gesture-required",
        ],
    )
    ctx = await browser.new_context(permissions=["microphone"], extra_http_headers={"ngrok-skip-browser-warning": "1"},
                                    ignore_https_errors=True)
    page = await ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    voice = wav.read_bytes()
    await page.route("**/__e2e/voice.wav", lambda route: route.fulfill(body=voice, content_type="audio/wav"))
    await page.add_init_script(INIT)
    if NO_SPEECH:
        await page.add_init_script("delete window.webkitSpeechRecognition; delete window.SpeechRecognition;")
    await page.goto(BASE + path)
    return browser, page, errors


async def main() -> int:
    sys.path.insert(0, str(HERE.parent / "backend"))
    from app.stt.refine import transcribe  # TokenRouter STT, same as the app uses

    async with async_playwright() as pw:
        import urllib.request

        import ssl

        urllib.request.urlopen(urllib.request.Request(BASE + "/api/reset", method="POST"), timeout=10,
                               context=ssl._create_unverified_context())
        cb, caller, cerr = await phone(pw, HERE / "caller_neutral.wav", "/caller")
        gb, granny, gerr = await phone(pw, HERE / "granny.wav", "/granny")
        await asyncio.sleep(1.5)
        await caller.get_by_label("Call Grandma").click()
        await granny.get_by_label("Answer").click(timeout=8000)
        t0 = time.time()
        await asyncio.sleep(LISTEN_S)
        results = {}
        for name, page, other in (("caller", caller, "granny"), ("granny", granny, "caller")):
            st = await page.evaluate("({rx: window.__rx, tx: window.__tx, played: window.__played, txPeak: window.__txPeak, gum: window.__gum, rxt: window.__rxt || [], txt: window.__txt || []})")
            out = HERE / f"received_by_{name}.wav"
            rms = save_wav(out, st["rx"])
            pcm = out.read_bytes()[44:]
            heard = await transcribe(pcm[: 16000 * 2 * 12], 16000) if pcm else None  # first 12 s is plenty
            rxt, txt = st["rxt"], st["txt"]
            gaps = sorted(b - a for a, b in zip(rxt, rxt[1:]))
            timing = {"rx_span_s": round((rxt[-1] - rxt[0]) / 1000, 1) if rxt else 0,
                      "tx_span_s": round((txt[-1] - txt[0]) / 1000, 1) if txt else 0,
                      "rx_gap_p50_ms": round(gaps[len(gaps) // 2]) if gaps else None,
                      "rx_gap_p99_ms": round(gaps[int(len(gaps) * 0.99)]) if gaps else None,
                      "rx_gaps_over_200ms": sum(g > 200 for g in gaps)}
            results[name] = {**timing, "sent_frames": st["tx"], "sent_peak": st["txPeak"], "mic_starts": st["gum"], "received_frames": len(st["rx"]), "played_buffers": st["played"],
                             "received_seconds": round(sum(len(f) for f in st["rx"]) / 16000, 1), "rms": round(rms), "jitter": await page.evaluate("window.__audioStats || null"), "heard_" + other: heard}
        await caller.get_by_label("End call").click()
        await asyncio.sleep(1)
        for b in (cb, gb):
            await b.close()
    print(json.dumps(results, indent=2))
    errs = [e for e in cerr + gerr if "speech" not in e.lower()]
    if errs:
        print("page errors:", errs[:5])
    ok = True
    for name, r in results.items():
        expect = ["dinner", "sunday", "pie"] if name == "granny" else ["cookies", "grandson"]
        heard = (r.get("heard_" + ("caller" if name == "granny" else "granny")) or "").lower()
        good = r["received_seconds"] > 6 and r["played_buffers"] >= r["received_frames"] > 0 and any(w in heard for w in expect)
        print(f"{'PASS' if good else 'FAIL'}  {name} heard the other side: {heard!r}")
        ok &= good
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
