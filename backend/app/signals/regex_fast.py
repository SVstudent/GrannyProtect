"""Fast path: keyword and digit detection on partial transcripts. Pure regex, well under 5 ms.

This powers the PII guard, which can't wait for a model, and gives the policy an instant first read
on each caller turn while the decision model is still thinking.
"""

import re
from dataclasses import dataclass, field

DIGIT_WORDS = {
    "zero": 1, "one": 1, "two": 1, "three": 1, "four": 1, "five": 1,
    "six": 1, "seven": 1, "eight": 1, "nine": 1, "won": 1, "to": 0, "too": 0, "for": 0, "ate": 0,
}
# "oh"/"to"/"for" count only inside a run that already has real digits, so "talk to" never starts a run.
WEAK_DIGIT_WORDS = {"oh": "0", "o": "0", "to": "2", "too": "2", "for": "4", "ate": "8"}
JOINERS = {"and", "uh", "um", "dash", "-", "then", "double", "triple"}

PII_PHRASES = [
    r"account number", r"card number", r"routing number", r"\bpin\b", r"pin number",
    r"social security", r"\bssn\b", r"security code", r"\bcvv\b", r"expiration date",
    r"my password", r"the password is", r"let me (find|get|grab) (it|my|the)", r"it'?s on the back",
    r"date of birth", r"my number is", r"the number is",
]
PII_RE = re.compile("|".join(PII_PHRASES), re.I)

CALLER_PATTERNS: dict[str, list[str]] = {
    "claims_authority": [
        r"\birs\b", r"internal revenue", r"\bpolice\b", r"sheriff", r"\bfbi\b", r"fraud (department|team|division)",
        r"\bmedicare\b", r"social security (administration|office)", r"\bchase\b", r"wells fargo", r"bank of america",
        r"(your|the) bank", r"microsoft", r"apple support", r"tech support", r"government", r"\bdea\b",
        r"warrant", r"court", r"amazon (security|support)",
    ],
    "requests_credentials": [
        r"account number", r"card number", r"routing", r"\bpin\b", r"social security number", r"\bssn\b",
        r"password", r"verify your (identity|account|information|details)", r"security code", r"\bcvv\b",
        r"one[- ]time (code|passcode)", r"verification code", r"login", r"date of birth",
    ],
    "requests_unusual_payment": [
        r"gift ?cards?", r"itunes", r"google play", r"\bwire\b", r"western union", r"moneygram", r"crypto",
        r"bitcoin", r"\bzelle\b", r"venmo", r"cash ?app", r"bail", r"\batm\b", r"prepaid", r"courier",
    ],
    "creates_urgency": [
        r"right now", r"immediately", r"\btoday\b", r"within (the hour|\d+ (minutes|hours))", r"urgent",
        r"before it'?s too late", r"don'?t hang up", r"stay on the line", r"as soon as possible", r"hurry",
        r"suspicious (charges|activity)", r"compromised", r"\bfrozen\b",
    ],
    "demands_secrecy": [
        r"don'?t tell", r"do not tell", r"keep (this|it) (between us|secret|quiet)", r"\bsecret\b",
        r"nobody (else )?(can|should) know", r"don'?t (call|talk to) (anyone|your)", r"confidential",
    ],
    "threatens": [
        r"\barrest", r"\bjail\b", r"warrant", r"\bfrozen\b", r"\bfreeze\b", r"suspend", r"lawsuit", r"deport",
        r"lose (all )?(your|the) (money|savings|benefits)", r"\bpenalt", r"in trouble", r"locked out",
    ],
}
CALLER_RE = {k: re.compile("|".join(v), re.I) for k, v in CALLER_PATTERNS.items()}

TOKEN_RE = re.compile(r"[a-z]+|\d+|-", re.I)


def digit_run(text: str) -> int:
    """Longest run of spoken digits, counting numerals ("4729" = 4) and number words ("four seven" = 2)."""
    best = cur = 0
    for tok in TOKEN_RE.findall(text.lower()):
        if tok.isdigit():
            cur += len(tok)
        elif tok in DIGIT_WORDS and DIGIT_WORDS[tok]:
            cur += 1
        elif tok in WEAK_DIGIT_WORDS and cur > 0:
            cur += 1
        elif tok in JOINERS and cur > 0:
            continue
        else:
            cur = 0
        best = max(best, cur)
    return best


def pii_phrase(text: str) -> str | None:
    m = PII_RE.search(text)
    return m.group(0) if m else None


@dataclass
class FastHits:
    hits: list[dict] = field(default_factory=list)
    digit_run: int = 0

    @property
    def kinds(self) -> set[str]:
        return {h["kind"] for h in self.hits}


def scan_granny(text: str) -> FastHits:
    out = FastHits(digit_run=digit_run(text))
    if p := pii_phrase(text):
        out.hits.append({"kind": "pii_phrase", "match": p})
    if out.digit_run:
        out.hits.append({"kind": "digits", "match": f"{out.digit_run} digit(s)"})
    return out


def scan_caller(text: str) -> FastHits:
    out = FastHits()
    for kind, rx in CALLER_RE.items():
        if m := rx.search(text):
            out.hits.append({"kind": kind, "match": m.group(0)})
    return out


# ---------- who the caller claims to be, and the safe way to check ----------
ORGS: list[tuple[str, str, str]] = [
    # (pattern, display name, safe next step)
    (r"\bchase\b", "Chase", "call Chase at the number on the back of your card"),
    (r"wells fargo", "Wells Fargo", "call Wells Fargo at the number on the back of your card"),
    (r"bank of america", "Bank of America", "call Bank of America at the number on the back of your card"),
    (r"\birs\b|internal revenue", "the IRS", "check irs.gov; the IRS always writes to you first and never calls for payment"),
    (r"social security", "Social Security", "call Social Security at 1-800-772-1213"),
    (r"\bmedicare\b", "Medicare", "call 1-800-MEDICARE, the number on your Medicare card"),
    (r"\bamazon\b", "Amazon", "open the Amazon app or website yourself"),
    (r"microsoft|apple support|tech support", "tech support", "just hang up; Microsoft and Apple never call you"),
    (r"\bpolice\b|sheriff|officer|\bfbi\b", "law enforcement", "call your local police on their public number"),
    (r"grand(ma|pa|mother|father|son|daughter)|it'?s me\b|i'?m your (son|daughter|nephew|niece|grandson|granddaughter)|your (son|daughter|grandson|granddaughter)\b",
     "a family member", "call your family directly on the number you already have"),
    (r"(your|the) bank|fraud (department|team)", "your bank", "call your bank at the number on the back of your card"),
]
_ORG_RE = [(re.compile(p, re.I), name, step) for p, name, step in ORGS]
DEFAULT_STEP = "hang up and call back using a number you already trust"


def claimed_org(text: str) -> tuple[str, str] | None:
    for rx, name, step in _ORG_RE:
        if rx.search(text):
            return name, step
    return None


# ---------- granny talking to the guardian ----------
WAKE_RE = re.compile(r"\b(guardian|gardian|guardians)\b", re.I)
HANGUP_INTENT_RE = re.compile(r"hang up|end (the|this) call|get rid of|make (it|them|him|her) stop|cut (it|them) off", re.I)


# ---------- account / card number redaction ----------
CARD_CONTEXT = re.compile(r"\b(credit|debit|card|visa|mastercard|master card|amex|american express|discover|expiration|cvv)\b", re.I)
ACCOUNT_CONTEXT = re.compile(r"\b(account|routing|checking|savings|bank)\b", re.I)


def number_kind(*texts: str) -> str:
    """'card' or 'account' from what's being talked about; 'number' if it isn't clear."""
    joined = " ".join(t for t in texts if t)
    if CARD_CONTEXT.search(joined):
        return "card"
    if ACCOUNT_CONTEXT.search(joined):
        return "account"
    return "number"


def trailing_digits(text: str) -> tuple[int, bool]:
    """(digits in the run at the very end of the text, whether a digit run has ended inside it).

    "my card is four one one"      -> (3, False)  still reading the number
    "four one one one, is that it" -> (0, True)   the number is over; she's talking normally again
    """
    run = 0
    ended = False
    for tok in TOKEN_RE.findall(text.lower()):
        is_digit = tok.isdigit() or DIGIT_WORDS.get(tok) or (tok in WEAK_DIGIT_WORDS and run > 0)
        if is_digit:
            run += len(tok) if tok.isdigit() else 1
        elif tok in JOINERS and run > 0:
            continue
        elif run > 0:
            run, ended = 0, True
    return run, ended


def mask_digits(text: str, min_digits: int = 2) -> tuple[str, bool]:
    """Replace spoken number runs of `min_digits`+ ("4111 1111", "four one one one") with ••••, for the caller's
    subtitles. Single numbers ("two cats", "at four") are left alone."""
    toks = re.findall(r"[A-Za-z']+|\d+|[^\sA-Za-z\d]+|\s+", text)
    out: list[str] = []
    i = 0
    masked = False
    while i < len(toks):
        low = toks[i].lower()
        if toks[i].isdigit() or DIGIT_WORDS.get(low):
            j, digits, last = i, 0, i
            while j < len(toks):
                t = toks[j].lower()
                if toks[j].isdigit() or DIGIT_WORDS.get(t) or (t in WEAK_DIGIT_WORDS and digits):
                    digits += len(t) if t.isdigit() else 1
                    last = j
                elif not (toks[j].isspace() or t in JOINERS or toks[j] in {",", "-"}):
                    break
                j += 1
            if digits >= min_digits:
                out.append("••••")
                masked = True
                i = last + 1
                continue
        out.append(toks[i])
        i += 1
    return "".join(out), masked
