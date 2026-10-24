"""Scam memory: caller lines from scams the guardian stopped on earlier real calls.
A new caller who repeats a remembered script is recognised on the spot (rule R2d_known_scam_script)."""

import difflib
import re

from . import store

THRESHOLD = 0.72
_WORD = re.compile(r"[a-z0-9']+")


def _norm(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def match(text: str, exclude_call: str | None = None) -> dict | None:
    """Best remembered caller line similar to `text`, or None."""
    q = _norm(text)
    if len(q.split()) < 5:
        return None
    qs = set(q.split())
    best = None
    for row in store.scam_memory():
        if row["call_id"] == exclude_call:
            continue
        m = _norm(row["text"])
        ms = set(m.split())
        if len(qs & ms) / max(len(qs | ms), 1) < 0.3:  # cheap prefilter before the real comparison
            continue
        ratio = difflib.SequenceMatcher(None, q, m).ratio()
        if ratio >= THRESHOLD and (best is None or ratio > best["similarity"]):
            best = {"similarity": round(ratio, 3), "past_call_id": row["call_id"], "past_when_ts": row["ts"],
                    "excerpt": row["text"], "org": row["org"]}
    return best
