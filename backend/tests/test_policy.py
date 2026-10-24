from app.policy.engine import PolicyState, evaluate
from app.signals.model import Signal, TurnSignals


def sigs(score=0.0, model_level=0, **ps):
    return TurnSignals(provider="test", scam_score=score, recommended_level=model_level,
                       signals={k: Signal(p=v, value=v >= 0.6) for k, v in ps.items()})


def test_monitor_by_default():
    d = evaluate(PolicyState(), sigs(0.1))
    assert d.rule_id == "R0_monitor" and d.level == 0 and not d.actions


def test_single_flag_caution():
    d = evaluate(PolicyState(), sigs(0.3, claims_authority=0.9))
    assert d.rule_id == "R1b_single_red_flag" and d.level == 1
    assert "whisper_to_granny" in d.allowed_tools and "terminate_call" not in d.allowed_tools


def test_two_flags_alert():
    d = evaluate(PolicyState(level=1), sigs(0.55, claims_authority=0.9, requests_credentials=0.9))
    assert d.rule_id == "R2b_two_red_flags" and d.level == 2
    assert [a["tool"] for a in d.actions] == ["whisper_to_granny", "vibrate_granny", "notify_family"]


def test_coercion_at_alert_terminates():
    d = evaluate(PolicyState(level=2), sigs(0.7, demands_secrecy=0.9))
    assert d.rule_id == "R3b_alert_plus_coercion" and d.level == 3
    assert d.actions == [{"tool": "terminate_call"}]


def test_secrecy_from_monitor_is_not_terminate():
    d = evaluate(PolicyState(level=0), sigs(0.4, demands_secrecy=0.9))
    assert d.level == 1


def test_repeat_pii_terminates():
    d = evaluate(PolicyState(level=1, pii_attempts=2), sigs(0.3))
    assert d.rule_id == "R3c_repeat_pii" and d.level == 3


def test_fast_path_capped_at_caution():
    d = evaluate(PolicyState(), sigs(0.99, claims_authority=0.9, requests_credentials=0.9), fast_path_only=True)
    assert d.level == 1 and d.capped


def test_levels_never_drop():
    d = evaluate(PolicyState(level=2), sigs(0.0))
    assert d.level == 2 and not d.escalated


def test_model_recommendation_needs_a_flag():
    assert evaluate(PolicyState(), sigs(0.3, model_level=2)).level == 0
    assert evaluate(PolicyState(), sigs(0.3, model_level=2, demands_secrecy=0.9, threatens=0.7)).rule_id == "R2b_two_red_flags"
    assert evaluate(PolicyState(), sigs(0.3, model_level=2, threatens=0.7)).rule_id == "R2c_model_alert_with_flag"


def test_deterministic():
    s = sigs(0.85, claims_authority=0.8, requests_credentials=0.7)
    assert evaluate(PolicyState(level=1), s) == evaluate(PolicyState(level=1), s)


def test_high_score_never_hangs_up_from_a_cold_start():
    """Terminate is irreversible: a 0.97 score at Caution raises to Alert first, so granny hears a warning."""
    d = evaluate(PolicyState(level=1), sigs(0.97, claims_authority=0.9, requests_credentials=0.9))
    assert d.level == 2 and d.rule_id == "R2a_score_high"
    d = evaluate(PolicyState(level=2), sigs(0.97, claims_authority=0.9, requests_credentials=0.9))
    assert d.level == 2  # a credential ask alone leaves room for granny to decline (and for redaction)
    d = evaluate(PolicyState(level=2), sigs(0.99, claims_authority=0.9, requests_credentials=0.9))
    assert d.level == 3 and d.rule_id == "R3a_score_critical"
