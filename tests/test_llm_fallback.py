"""LLM fallback paths — no key/network required (D8). FakeClient covers the live path."""
from __future__ import annotations

from electricopilot.engine import size
from electricopilot.llm.explain import explain_render_template
from electricopilot.llm.verify import verify_deterministic_check
from electricopilot.pipeline import run

from .conftest import FakeClient, make_request

CASE1 = dict(P=4600, U=230, ph=1, pf=1.0, ins="PVC", amb=35, grp=1, L=25, dev="MCB", iscc=800)


def test_template_explanation_is_clean():
    r = size(make_request(**CASE1))
    n = explain_render_template(r)
    assert n.model == "" and n.provenance_ok
    assert "4" in n.text  # references the chosen section


def test_deterministic_verify_true_for_valid():
    req = make_request(**CASE1)
    r = size(req)
    assert verify_deterministic_check(req, r) is True


def test_pipeline_offline_fallback():
    req = make_request(**CASE1)
    out = run(req, client=None, explain=True, verify=True)
    assert out.result.overall_status == "PASS"
    assert out.narrative is not None and out.narrative.model == ""   # template used
    assert out.verdict is not None and out.verdict.deterministic_ok
    assert out.verdict.model == ""                                    # no live model


def test_pipeline_live_path_with_fake_client():
    req = make_request(**CASE1)
    fake = FakeClient('{"agrees": true, "issues": []}')  # verify JSON; explain reuses text
    # explain call returns the same string; provenance will flag JSON braces? no digits smuggled.
    out = run(req, client=fake, explain=False, verify=True, model_strong="fake/model")
    assert fake.calls == 1
    assert out.verdict is not None and out.verdict.agrees is True
    assert out.verdict.model == "fake/model" and out.verdict.deterministic_ok


def test_pipeline_live_explain_provenance_downgrade():
    req = make_request(**CASE1)
    # narrative smuggles 999 (absent) -> strict provenance -> downgrade to NEEDS_REVIEW
    fake = FakeClient("Возьмите 999 мм² с запасом.")
    out = run(req, client=fake, explain=True, verify=False,
              strict_provenance=True, model_fast="fake/model")
    assert out.narrative is not None and not out.narrative.provenance_ok
    assert "999" in out.narrative.unverified_numbers
    assert out.result.overall_status == "NEEDS_REVIEW"
