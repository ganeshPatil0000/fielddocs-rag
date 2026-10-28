from app.rag.prompts import NO_ANSWER
from app.rag.validation import parse_grounding_verdict, validate_citations


def test_valid_citations_are_kept():
    r = validate_citations("Clean the connector [1]. Measure power [2].", num_sources=3)
    assert r.cited == [1, 2]
    assert r.grounded and not r.is_refusal


def test_invented_citations_are_stripped():
    r = validate_citations("Reset the ONT [1][7].", num_sources=2)
    assert r.cited == [1]
    assert r.invalid_citations == [7]
    assert "[7]" not in r.answer


def test_uncited_answer_is_refused_in_strict_mode():
    r = validate_citations("Just reboot it, that always works.", num_sources=3, strict=True)
    assert r.answer == NO_ANSWER
    assert r.is_refusal and not r.grounded


def test_uncited_answer_is_flagged_in_lenient_mode():
    r = validate_citations("Just reboot it.", num_sources=3, strict=False)
    assert r.answer == "Just reboot it."
    assert not r.grounded and not r.is_refusal


def test_model_refusal_is_normalised():
    r = validate_citations(NO_ANSWER, num_sources=3)
    assert r.is_refusal and r.grounded


def test_grounding_verdict_fails_closed():
    assert parse_grounding_verdict('{"grounded": true, "reason": "ok"}') == (True, "ok")
    assert parse_grounding_verdict('Sure! {"grounded": false, "reason": "x"}')[0] is False
    assert parse_grounding_verdict("I think it's fine")[0] is False
