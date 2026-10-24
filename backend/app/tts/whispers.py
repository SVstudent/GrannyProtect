"""Everything granny can hear, and the voice that says it.

Every line is either fixed or built from a small template (who the caller claims to be × what they want),
so the full set is known in advance. All of it is pre-generated with a natural voice through TokenRouter,
cached on disk, and played instantly; no whisper ever waits on a model or falls back to a robotic voice
once the cache is warm. (`make voices` warms it ahead of time.)
"""

import asyncio
import base64
import difflib
import hashlib
import json
import time

from ..config import settings
from ..signals import regex_fast

LINES = {
    "caution": "Be careful, this might not be who they say.",
    "alert": "They're asking for your bank details. Your bank never calls for that. Just say: I'll call you back myself.",
    "pii": "I stopped that, so they didn't hear your number.",
    "redact_card": "I kept your card number private.",
    "redact_account": "I kept your account number private.",
    "redact_number": "I kept that number private.",
    "end": "I've ended this call. It was a scam. You did nothing wrong.",
    "end_request": "I've ended the call for you. You did the right thing.",
    "closing": "This call has been ended by Granny-Protect-5000.",
    "ask_ok": "It sounds okay so far, and I'm listening. Never share numbers on a call.",
    "ask_unsure": "I'm not sure yet. Don't share anything. You can say you'll call back.",
    "ask_scam": "This looks like a scam. You can just hang up, or say: Guardian, hang up, and I'll do it.",
    "ask_hangup": "Okay. I'm ending the call now.",
    "verify_family": "I'm asking a family question to make sure it's really them.",
    "verify_callback": "I'm checking whether they'll let you call them back.",
    "verify_pass_family": "They answered correctly. It's really them. I'll keep listening.",
    "verify_pass_callback": "They agreed you can call back. To be safe, hang up and call the number on your card.",
    "verify_fail": "They couldn't answer my question. This is a scam.",
    "verify_timeout": "They didn't answer my question. Please don't send anything.",
}

ORG_NAMES = [name for _, name, _ in regex_fast.ORGS]


def _cap(s: str) -> str:
    return s[0].upper() + s[1:]


def alert_line(kind: str, org: str | None) -> str:
    """The level-2 warning: names who they claim to be and gives granny one safe sentence to say."""
    if org == "a family member":
        if kind == "payment":
            return "A real grandchild can wait while you call their parents. Please don't send any money."
        return "Let's check first. Hang up, and call your family on the number you already have."
    if kind == "payment":
        return f"They want gift cards or a wire transfer. {_cap(org or 'your bank')} would never ask for that. Please don't pay anything."
    if kind == "credentials":
        if org in (None, "your bank"):
            return LINES["alert"]
        return f"They're asking for your private details. {_cap(org)} never calls for that. Just say: I'll call you back myself."
    return "This sounds like a scam. You can just hang up. I'm right here with you."


def safe_step_line(step: str) -> str:
    return f"If you're worried, {step}."


def ask_unsure_line(org: str | None) -> str:
    if not org:
        return LINES["ask_unsure"]
    return f"They say they're {org}, but I can't confirm that. Don't share anything. You can say you'll call back."


def all_lines() -> list[str]:
    out = list(LINES.values())
    for org in [None, *ORG_NAMES]:
        for kind in ("credentials", "payment", "generic"):
            out.append(alert_line(kind, org))
        out.append(ask_unsure_line(org))
    out += [safe_step_line(step) for _, _, step in regex_fast.ORGS] + [safe_step_line(regex_fast.DEFAULT_STEP)]
    from .. import verify  # the questions the guardian may ask callers out loud

    out += [verify.family_prompt(q) for q in verify.public_facts()] + [verify.CALLBACK_QUESTION]
    return list(dict.fromkeys(out))


# ---------- the voice ----------
_cache: dict[str, str] = {}  # text -> base64 mp3

_TTS_SYSTEM = (
    "You are the gentle voice of a guardian speaking privately into an elderly person's ear during a phone call. "
    "Speak warmly and calmly, a little slowly, like a kind grandchild or a caring nurse: reassuring, never alarmed, "
    "with natural pauses at commas. Output only the script text, verbatim."
)


def _key(text: str) -> str:
    return hashlib.sha1(f"{settings.tts_model}|{settings.tts_voice}|{_TTS_SYSTEM}|{text}".encode()).hexdigest()[:16]


def cached_audio(text: str) -> str | None:
    return _cache.get(text)


async def _synth(text: str) -> str | None:
    from openai import AsyncOpenAI

    from ..supervisor import price, sup

    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=180)
    t0 = time.perf_counter()
    resp = await client.chat.completions.create(
        model=settings.tts_model,
        modalities=["text", "audio"],
        audio={"voice": settings.tts_voice, "format": "mp3"},
        messages=[{"role": "system", "content": _TTS_SYSTEM},
                  {"role": "user", "content": f'Repeat this sentence back word for word, and say nothing else: "{text}"'}],
    )
    await sup.record("tts", price(settings.tts_model, resp.usage), (time.perf_counter() - t0) * 1000, None)
    audio = resp.choices[0].message.audio
    if not audio or not audio.data:
        return None
    # The audio model sometimes paraphrases; only keep clips that say what we wrote.
    said = (audio.transcript or "").lower()
    if difflib.SequenceMatcher(None, said, text.lower()).ratio() < 0.85:
        print(f"[tts] rejected paraphrase for {text!r}: {said!r}", flush=True)
        return None
    return audio.data


async def pregenerate(concurrency: int = 4) -> None:
    """Load cached clips from disk; synthesize missing ones in the background."""
    if settings.tts_provider != "tokenrouter" or not settings.llm_api_key:
        return
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    lines = all_lines()
    missing = []
    for text in lines:
        f = settings.cache_dir / f"tts_{_key(text)}.mp3"
        if f.exists():
            _cache[text] = base64.b64encode(f.read_bytes()).decode()
        else:
            missing.append((text, f))
    print(f"[tts] {len(_cache)}/{len(lines)} whispers ready; generating {len(missing)} more in the background", flush=True)
    gate = asyncio.Semaphore(concurrency)

    async def one(text, f):
        async with gate:
            for _ in range(3):
                try:
                    if data := await _synth(text):
                        f.write_bytes(base64.b64decode(data))
                        _cache[text] = data
                        return
                except Exception as e:  # noqa: BLE001
                    print(f"[tts] failed {text!r}: {e!r}", flush=True)
                    await asyncio.sleep(3)

    await asyncio.gather(*(one(t, f) for t, f in missing))
    print(f"[tts] {len(_cache)}/{len(lines)} whispers have the natural voice", flush=True)


if __name__ == "__main__":
    asyncio.run(pregenerate(concurrency=int(__import__("os").getenv("TTS_CONCURRENCY", "4"))))
    print(json.dumps({"ready": len(_cache), "total": len(all_lines())}))
