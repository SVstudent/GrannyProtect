import pytest

from app.signals.regex_fast import digit_run, pii_phrase, scan_caller


@pytest.mark.parametrize("text,expected", [
    ("it's four, seven, two, nine", 4),
    ("my number is 4729 1234", 8),
    ("four seven to nine", 4),          # "to" heard for "two" inside a run
    ("I'll talk to you at four", 1),
    ("Oh hello dear", 0),               # "oh" only counts inside a digit run
    ("five five five, one two three four", 7),
    ("nineteen forty", 0),
])
def test_digit_run(text, expected):
    assert digit_run(text) == expected


def test_pii_phrases():
    assert pii_phrase("Oh my, let me find it...")
    assert pii_phrase("my account number is")
    assert pii_phrase("what's your PIN")
    assert pii_phrase("see you Sunday") is None


def test_caller_flags():
    kinds = scan_caller("This is Mark from Chase's fraud department, verify your account number").kinds
    assert {"claims_authority", "requests_credentials"} <= kinds
    assert "requests_unusual_payment" in scan_caller("go buy Google Play gift cards").kinds
    assert scan_caller("Are we still on for dinner Sunday?").kinds == set()
