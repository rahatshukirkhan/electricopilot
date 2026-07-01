"""Studio visualization data (deterministic, docs/10)."""
from __future__ import annotations

from electricopilot.viz import build_visuals

from .conftest import make_request

CASE2 = dict(P=15000, U=400, ph=3, pf=0.85, ins="XLPE", amb=40, grp=3, L=50, dev="gG_fuse", iscc=2500)


def test_build_visuals_shape():
    v = build_visuals(make_request(**CASE2))
    assert set(v) >= {"result", "sweep", "vd_profile", "derating", "tcc", "sld",
                      "data_provenance_note", "disclaimer"}
    assert v["result"]["overall_status"] == "PASS"
    assert v["data_provenance_note"]  # illustrative pack → note present


def test_sweep_chosen_and_required():
    v = build_visuals(make_request(**CASE2))
    s = v["sweep"]
    assert s["chosen_mm2"] == 10 and len(s["rows"]) == 16
    assert abs(s["required_iz_a"] - 35.31) < 0.1
    chosen = next(r for r in s["rows"] if r["section_mm2"] == 10)
    assert chosen["amp_ok"] and chosen["vd_ok"] and chosen["sc_ok"]


def test_tcc_curves_and_coordination():
    v = build_visuals(make_request(**CASE2))
    t = v["tcc"]
    assert t["coordinated"] is True
    assert len(t["cable"]["withstand"]) > 10 and len(t["device"]["min"]) > 10
    assert t["markers"]["Iscc"] == 2500
    # cable withstand is the real adiabatic t=(k·S/I)^2 → monotdecreasing in I
    xs = [p[0] for p in t["cable"]["withstand"]]
    ts = [p[1] for p in t["cable"]["withstand"]]
    assert xs == sorted(xs) and ts == sorted(ts, reverse=True)


def test_derating_waterfall():
    v = build_visuals(make_request(**CASE2))
    d = v["derating"]
    assert len(d["stages"]) == 3
    assert d["stages"][0]["value"] >= d["stages"][-1]["value"]  # It ≥ Iz after derating
    assert abs(d["Iz_a"] - 44.55) < 0.1
