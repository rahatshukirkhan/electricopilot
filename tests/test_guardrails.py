"""Numeric-provenance guardrail (both directions), SignOff invariant, disclaimer."""
from __future__ import annotations

from electricopilot.engine import size
from electricopilot.guardrails import apply_provenance_downgrade, check_numeric_provenance
from electricopilot.models import LlmNarrative
from electricopilot.report import render_markdown

from .conftest import make_request

CASE1 = dict(P=4600, U=230, ph=1, pf=1.0, ins="PVC", amb=35, grp=1, L=25, dev="MCB", iscc=800)


def test_clean_narrative_passes():
    r = size(make_request(**CASE1))  # S=4, Iz=27, In=20
    text = "Выбрано сечение 4 мм², IZ 27 A при аппарате In 20 A."
    ok, unverified = check_numeric_provenance(text, r, strict=True)
    assert ok and unverified == []


def test_smuggled_number_flagged_and_downgraded():
    r = size(make_request(**CASE1))
    text = "Рекомендую увеличить до 777 мм² для запаса."  # 777 absent from result
    ok, unverified = check_numeric_provenance(text, r, strict=True)
    assert not ok and "777" in unverified
    narrative = LlmNarrative(text=text, model="x", provenance_ok=ok, unverified_numbers=unverified)
    downgraded = apply_provenance_downgrade(r, narrative)
    assert downgraded.overall_status == "NEEDS_REVIEW"
    assert r.overall_status == "PASS"  # original untouched (copy semantics)


def test_citation_numbers_not_flagged():
    r = size(make_request(**CASE1))
    text = "Согласно IEC 60364-4-43 §433.1 условие I2 ≤ 1.45·IZ выполнено."
    ok, unverified = check_numeric_provenance(text, r, strict=True)
    assert ok, unverified


def test_space_grouped_thousands_caught():
    """Regression: '25 000' must not tokenize into ['25','000'] and slip through."""
    r = size(make_request(**CASE1))  # In=20, S=4; no 25000 anywhere
    ok, unverified = check_numeric_provenance("Ток КЗ 25 000 А.", r, strict=True)
    assert not ok and "25 000" in unverified


def test_unrelated_standard_rating_flagged():
    """Regression: a standard rating not used in THIS design must be flagged (no ladder leak)."""
    r = size(make_request(**CASE1))  # selected In=20 A
    for smuggled in ("Требуется 315 A предохранитель.", "Рекомендую автомат 250 А."):
        ok, unverified = check_numeric_provenance(smuggled, r, strict=True)
        assert not ok, smuggled


def test_citation_digit_not_reusable_as_value():
    """Regression: '43' from IEC 60364-4-43 must not license a smuggled '43 A'."""
    r = size(make_request(**CASE1))
    ok, unverified = check_numeric_provenance("Провод рассчитан на 43 A длительно.", r, strict=True)
    assert not ok and "43" in unverified


def test_derived_coordination_value_passes():
    """1.45·IZ (shown in the overload check detail, e.g. 39.15 for case 1) is an audited
    derived value — a narrative citing it must NOT be flagged."""
    r = size(make_request(**CASE1))  # Iz=27 → 1.45·27 = 39.15 appears in the check detail
    ok, unverified = check_numeric_provenance("Условие I2 ≤ 1,45·IZ = 39,15 A выполнено.", r, strict=True)
    assert ok, unverified


def test_downgrade_never_improves_fail():
    """Regression: a provenance failure must not turn FAIL into NEEDS_REVIEW."""
    r = size(make_request(**CASE1)).model_copy(update={"overall_status": "FAIL"})
    bad = LlmNarrative(text="999", model="x", provenance_ok=False, unverified_numbers=["999"])
    out = apply_provenance_downgrade(r, bad)
    assert out.overall_status == "FAIL"  # stayed FAIL, not softened to NEEDS_REVIEW


def test_signoff_default_and_disclaimer():
    r = size(make_request(**CASE1))
    assert r.signoff.status == "UNSIGNED_ADVISORY"
    assert r.disclaimer
    md = render_markdown(r)
    assert "НЕ ПОДПИСАНО" in md
    assert "СИНТЕТИЧЕСК" in md.upper()  # provenance note rendered for illustrative pack
