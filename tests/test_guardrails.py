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


def test_signoff_default_and_disclaimer():
    r = size(make_request(**CASE1))
    assert r.signoff.status == "UNSIGNED_ADVISORY"
    assert r.disclaimer
    md = render_markdown(r)
    assert "НЕ ПОДПИСАНО" in md
    assert "СИНТЕТИЧЕСК" in md.upper()  # provenance note rendered for illustrative pack
