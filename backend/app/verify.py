"""Identity verification: when the guardian can't tell a real caller from a scammer, it asks one question.

Family claims get a shared-secret question from family_facts.yaml (a stranger can't know the answer).
Institution claims get the real-world test: "Can she call you back on the number on her card?" A real bank
says yes; a scammer resists or pressures. Both are judged deterministically and explained in plain English.
"""

from __future__ import annotations

import difflib
import itertools
import re
from pathlib import Path

import yaml

FACTS_FILE = Path(__file__).resolve().parents[1] / "family_facts.yaml"
_rotation = itertools.count()

CALLBACK_QUESTION = "Quick check before we continue: is it okay if she hangs up and calls you back on the number on her card?"
_DONT_KNOW = re.compile(r"\b(don'?t (know|remember)|can'?t remember|not sure|forgot|no idea|why does (that|it) matter|"
                        r"that doesn'?t matter|no time for this|just send|please just)\b", re.I)
# Consent must be about the call-back itself; a stray "okay" or room noise must never pass.
_CALLBACK_YES = re.compile(r"(call (us|me|the bank|the number|them)( back)?|call back|on the back of (your|her) card|"
                           r"official number|that'?s (fine|okay|ok)|of course|absolutely|please do|go ahead|no problem)", re.I)
_CALLBACK_NO = re.compile(r"(\bno\b(?! problem)|don'?t hang up|stay on the line|\bcan'?t\b|\bcannot\b|urgent|right now|"
                          r"too late|do this now|don'?t call|won'?t work|frozen|locked|no need to call)", re.I)


def load_facts() -> dict:
    try:
        return yaml.safe_load(FACTS_FILE.read_text()) or {}
    except FileNotFoundError:
        return {}


def public_facts() -> list[str]:
    return [f["question"] for f in load_facts().get("facts", [])]


def pick_family_question() -> dict | None:
    data = load_facts()
    facts = data.get("facts") or []
    if not facts:
        return None
    i = next(_rotation) % len(facts) if data.get("rotate") else 0
    return facts[i]


def family_prompt(question: str) -> str:
    return f"Quick check before we continue: {question}"


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", s.lower()))


def judge_family(answer: str, accepted: list[str]) -> tuple[bool, float, str]:
    """(passed, match 0..1, plain-English reason). Accepts an answer that contains an accepted phrase (fuzzy)."""
    said = _norm(answer)
    if not said:
        return False, 0.0, "no answer"
    best = 0.0
    words = said.split()
    for acc in accepted:
        a = _norm(acc)
        n = len(a.split())
        windows = [" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1))]
        best = max([best, *(difflib.SequenceMatcher(None, a, w).ratio() for w in windows)])
    if best >= 0.8:
        return True, round(best, 2), f"answered \"{answer.strip()}\", which matches the family fact"
    if _DONT_KNOW.search(answer):
        return False, round(best, 2), f"dodged the question (\"{answer.strip()}\")"
    return False, round(best, 2), f"answered \"{answer.strip()}\", which is wrong"


def judge_callback(answer: str) -> tuple[bool | None, float, str]:
    """True = agreed to a call-back (what real institutions do); False = resisted; None = unclear."""
    no, yes = bool(_CALLBACK_NO.search(answer)), bool(_CALLBACK_YES.search(answer))
    if no:
        return False, 0.0, f"resisted a call-back (\"{answer.strip()}\"), which real banks never do"
    if yes:
        return True, 1.0, f"agreed she can call back on the official number (\"{answer.strip()}\")"
    return None, 0.5, f"gave an unclear answer (\"{answer.strip()}\")"
