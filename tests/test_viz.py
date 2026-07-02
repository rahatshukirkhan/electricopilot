"""Studio visualization data (deterministic, docs/10)."""
from __future__ import annotations

from electricopilot.data.loader import load_data_pack
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


def test_build_visuals_pue_rk_b1_partial_coverage():
    """pue-rk B1/Cu: viz must not crash on sections the pack doesn't cover (it stops at
    120 mm²), and TCC degrades gracefully since pue-rk carries no device trip curves."""
    pack = load_data_pack("pue-rk")
    v = build_visuals(
        make_request(method="B1", material="Cu", P=3500, U=230, ph=1, pf=1.0, ins="PVC",
                     amb=25, grp=1, L=30, dev="MCB", iscc=800),
        data_pack=pack)
    assert v["result"]["overall_status"] == "PASS"
    sections = [r["section_mm2"] for r in v["sweep"]["rows"]]
    assert sections and max(sections) == 120  # only the B1-covered sections are swept
    assert v["tcc"]["device"]["available"] is False  # no trip curves in pue-rk
    assert len(v["tcc"]["cable"]["withstand"]) > 10  # real cable adiabatic curve still drawn
    # public_standard → the public-source note, NOT the "synthetic values" illustrative one
    assert "публичного государственного стандарта" in v["data_provenance_note"]
    assert "СИНТЕТИЧЕСКИЕ" not in v["data_provenance_note"]
