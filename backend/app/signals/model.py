"""Decision model: the same fixed, typed questions about the rolling transcript on every finished caller turn.

Providers:
  regex  - keyword fast path (also the mock); instant, but blind to negation and context
  jev    - TypeSafe Jev via TokenRouter /api/alpha/decisions; typed noul / score / choice answers
  llm    - JSON output from any chat model via TokenRouter, same questions
"""

import asyncio
import json
import re
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

from ..config import settings
from . import regex_fast

SIGNAL_NAMES = [
    "claims_authority",
    "requests_credentials",
    "requests_unusual_payment",
    "creates_urgency",
    "demands_secrecy",
    "threatens",
]
LEVEL_NAMES = ["monitor", "caution", "alert", "terminate"]

QUESTIONS = {
    "claims_authority": "Does the caller claim to be from a bank, government agency (IRS, Social Security, Medicare, police, court), a big tech company, or another authority?",
    "requests_credentials": "Is the caller asking the person to say or confirm private credentials: account or card numbers, PIN, SSN, passwords, security or one-time codes?",
    "requests_unusual_payment": "Is the caller asking for payment by gift cards, wire transfer, crypto, Zelle/Venmo/Cash App, cash courier, or moving money to a 'safe' account?",
    "creates_urgency": "Is the caller pressuring the person to act immediately or stay on the line, with a deadline or alarming claim?",
    "demands_secrecy": "Is the caller telling the person to keep the call secret or not to tell family, the bank, or anyone else?",
    "threatens": "Is the caller threatening arrest, legal action, frozen or lost accounts, lost benefits, or other harm?",
}

SCAM_SIGNS = (Path(__file__).parent / "scam_signs.md").read_text()

_NEGATION = re.compile(r"\b(never|won'?t|will not|don'?t need|do not need|not ask|no one will)\b[^.?!]{0,40}$", re.I)


class Signal(BaseModel):
    p: float
    value: bool
    evidence: str = ""


class TurnSignals(BaseModel):
    provider: str
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    claimed_org: str | None = None
    scam_score: float
    recommended_level: int
    signals: dict[str, Signal]
    rationale: str = ""

    def p(self, name: str) -> float:
        s = self.signals.get(name)
        return s.p if s else 0.0


def _score_from_ps(ps: dict[str, float]) -> float:
    weights = {
        "claims_authority": 0.3, "requests_credentials": 0.4, "requests_unusual_payment": 0.45,
        "creates_urgency": 0.2, "demands_secrecy": 0.45, "threatens": 0.35,
    }
    keep = 1.0
    for name, w in weights.items():
        keep *= 1 - w * ps.get(name, 0.0)
    return round(1 - keep, 3)


def _level_from(score: float, n_flags: int) -> int:
    if score >= 0.95:
        return 3
    if score >= 0.8 or n_flags >= 2:
        return 2
    if score >= 0.5 or n_flags >= 1:
        return 1
    return 0


def regex_signals(caller_turns: list[str]) -> TurnSignals:
    """Keyword read of the rolling caller transcript. Hits in the latest turn weigh slightly more."""
    t0 = time.perf_counter()
    recent = caller_turns[-8:]
    sigs: dict[str, Signal] = {}
    for name in SIGNAL_NAMES:
        p, ev = 0.05, ""
        for i, turn in enumerate(recent):
            m = regex_fast.CALLER_RE[name].search(turn)
            if not m or _NEGATION.search(turn[: m.start()]):
                continue
            p, ev = (0.9 if i == len(recent) - 1 else 0.85), m.group(0)
        sigs[name] = Signal(p=p, value=p >= 0.6, evidence=ev)
    ps = {k: v.p for k, v in sigs.items()}
    score = _score_from_ps(ps)
    flags = sum(1 for k, v in sigs.items() if v.value and k != "creates_urgency")  # urgency alone isn't a red flag
    return TurnSignals(
        provider="regex",
        latency_ms=round((time.perf_counter() - t0) * 1000, 2),
        scam_score=score,
        recommended_level=_level_from(score, flags),
        signals=sigs,
        rationale="Keyword fast path over the rolling caller transcript.",
    )


def _transcript_block(lines: list[tuple[str, str]]) -> str:
    return "\n".join(f"{'Caller' if who == 'caller' else 'Protected person'}: {text}" for who, text in lines[-16:])


async def jev_signals(lines: list[tuple[str, str]]) -> TurnSignals:
    t0 = time.perf_counter()
    questions: dict = {
        name: {"type": "noul", "instructions": q, "criteria": {"true": "yes, clearly in the transcript", "false": "no"}}
        for name, q in QUESTIONS.items()
    }
    questions["scam_score"] = {
        "type": "score",
        "instructions": "How likely is it that this phone call is a scam targeting the protected person?",
        "criteria": ["clearly legitimate", "probably legitimate", "unclear", "probably a scam", "clearly a scam"],
    }
    questions["recommended_level"] = {
        "type": "choice",
        "instructions": "What protection level should a guardian agent take right now?",
        "criteria": {
            "monitor": "an ordinary call, no warning signs",
            "caution": "one warning sign; quietly warn the person",
            "alert": "a clear scam pattern, such as an authority claim plus a request for credentials or payment",
            "terminate": "coercion, threats, secrecy demands, or theft in progress during a scam",
        },
    }
    body = {
        "model": settings.jev_model,
        "state": [
            "Reference: common phone scam patterns.\n" + SCAM_SIGNS,
            "Live phone call transcript (most recent last):\n" + _transcript_block(lines),
        ],
        "questions": questions,
    }
    async with httpx.AsyncClient(timeout=settings.signals_timeout_s) as client:
        r = await client.post(settings.jev_url, json=body, headers={"Authorization": f"Bearer {settings.llm_api_key}"})
        r.raise_for_status()
        data = r.json()
    answers = data.get("answers", data)
    cost = float((data.get("usage") or {}).get("cost") or 0.0)
    sigs = {}
    for name in SIGNAL_NAMES:
        p = float(answers.get(name, {}).get("noul", 0.0))
        sigs[name] = Signal(p=round(p, 3), value=p >= 0.6)
    sc = answers.get("scam_score", {})
    score = round(float(sc.get("score", 0.0)) / 4.0, 3)
    choice = answers.get("recommended_level", {}).get("choice", "monitor")
    probs = answers.get("recommended_level", {}).get("probabilities", {})
    return TurnSignals(
        provider=f"jev ({data.get('model', settings.jev_model)})",
        latency_ms=round((time.perf_counter() - t0) * 1000),
        cost_usd=cost,
        scam_score=score,
        recommended_level=LEVEL_NAMES.index(choice) if choice in LEVEL_NAMES else 0,
        signals=sigs,
        rationale=f"Jev level choice {choice} ({json.dumps(probs)}); score confidence {sc.get('confidence')}",
    )


_LLM_SYSTEM = """You are the signal classifier inside Granny-Protect-5000, which protects an elderly person on a live phone call.
Answer the same fixed questions about the transcript every time. Judge only the CALLER's behaviour, and account for negation
(a real bank saying "we will never ask for your PIN" is not a credential request).

Questions:
{questions}

Reference of scam patterns:
{signs}

Reply with JSON only:
{{"signals": {{"<name>": {{"p": <0..1 probability>, "evidence": "<short quote or empty>"}}, ...all six...}},
  "scam_score": <0..1>, "recommended_level": "monitor"|"caution"|"alert"|"terminate", "rationale": "<one sentence>"}}"""


async def llm_signals(lines: list[tuple[str, str]]) -> TurnSignals:
    from openai import AsyncOpenAI

    t0 = time.perf_counter()
    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=settings.signals_timeout_s)
    qs = "\n".join(f"- {k}: {v}" for k, v in QUESTIONS.items())
    resp = await client.chat.completions.create(
        model=settings.signals_llm_model,
        messages=[
            {"role": "system", "content": _LLM_SYSTEM.format(questions=qs, signs=SCAM_SIGNS)},
            {"role": "user", "content": "Transcript (most recent last):\n" + _transcript_block(lines)},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=600,
    )
    raw = resp.choices[0].message.content or "{}"
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    from ..supervisor import price

    cost = price(settings.signals_llm_model, resp.usage)
    sigs = {}
    for name in SIGNAL_NAMES:
        d = data.get("signals", {}).get(name, {})
        p = float(d.get("p", 0.0)) if isinstance(d, dict) else float(d or 0)
        sigs[name] = Signal(p=round(p, 3), value=p >= 0.6, evidence=str(d.get("evidence", "")) if isinstance(d, dict) else "")
    lvl = str(data.get("recommended_level", "monitor")).lower()
    return TurnSignals(
        provider=f"llm ({settings.signals_llm_model})",
        latency_ms=round((time.perf_counter() - t0) * 1000),
        cost_usd=cost,
        scam_score=round(float(data.get("scam_score", 0.0)), 3),
        recommended_level=LEVEL_NAMES.index(lvl) if lvl in LEVEL_NAMES else 0,
        signals=sigs,
        rationale=str(data.get("rationale", "")),
    )


async def model_signals(lines: list[tuple[str, str]], call_id: str | None = None) -> TurnSignals | None:
    """Run the configured decision model under supervision. Falls back jev -> llm -> None (regex)."""
    from ..supervisor import sup

    provider = settings.signals_provider
    if provider == "mock" or not settings.llm_api_key:
        return None
    order = {"jev": [("jev", jev_signals), ("agent", llm_signals)], "llm": [("agent", llm_signals)]}.get(provider, [])
    for health_key, fn in order:
        if not sup.allow(health_key):
            continue
        t0 = time.perf_counter()
        try:
            sig = await asyncio.wait_for(fn(lines), timeout=settings.signals_timeout_s + 1)
        except Exception as e:  # noqa: BLE001 - any provider failure falls through to the next one
            await sup.failure(health_key, e, "the next signal provider")
            continue
        await sup.record(health_key, sig.cost_usd, (time.perf_counter() - t0) * 1000, call_id)
        return sig
    return None
