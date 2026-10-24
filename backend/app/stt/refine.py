"""Hybrid STT, server half: after the browser marks an utterance final, transcribe that utterance's audio
with an audio-capable model through TokenRouter. Browser partials stay in charge of the PII guard; this
gives the agent and the report an accurate transcript (numbers, names) a few seconds later."""

import base64
import io
import re
import wave

from ..config import settings


# Audio chat models sometimes reply to the speech instead of transcribing it. Never let a reply become a transcript.
_ASSISTANT_REPLY = re.compile(
    r"^(i'?m sorry|sorry,|i (can'?t|cannot|don'?t understand)|could you (please )?(clarify|repeat)|as an ai|"
    r"here is|here'?s the transcript|the (audio|speaker|transcript)|transcript:|no speech|there is no)", re.I)


def looks_like_reply(text: str) -> bool:
    t = text.strip().strip('"')
    # Speaker labels and markdown ("**Caller:** ...") mean the model wrote a script, not a transcript.
    return bool(_ASSISTANT_REPLY.search(t) or "**" in t or re.match(r"^\W*(caller|speaker|person|granny|user)\s*\d*\s*:", t, re.I))


def pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


async def transcribe(pcm: bytes, rate: int, call_id: str | None = None) -> str | None:
    import time

    from ..supervisor import price, sup

    if not settings.llm_api_key or not sup.allow("stt"):
        return None
    from openai import AsyncOpenAI

    t0 = time.perf_counter()
    try:
        client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=30)
        audio = base64.b64encode(pcm_to_wav(pcm, rate)).decode()
        resp = await client.chat.completions.create(
            model=settings.stt_model,
            temperature=0,
            max_tokens=300,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": "You are a speech-to-text engine, not an assistant. Write down exactly the words spoken in "
                                         "this phone-call audio, verbatim. Never answer, comment, or ask questions. "
                                         "If there is no clear speech, output nothing."},
                {"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}},
            ]}],
        )
        await sup.record("stt", price(settings.stt_model, resp.usage), (time.perf_counter() - t0) * 1000, call_id)
        text = (resp.choices[0].message.content or "").strip().strip('"')
        return None if looks_like_reply(text) else text
    except Exception as e:  # noqa: BLE001
        await sup.failure("stt", e, "the browser transcript")
        return None
