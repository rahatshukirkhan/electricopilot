"""Golden domain cases — assert the LITERAL doc-06 §6.5 constants (not reference_calc)."""
from __future__ import annotations

import pytest

from electricopilot.data.loader import load_data_pack
from electricopilot.engine import size
from electricopilot.exceptions import DataPackError

from .conftest import make_request

# (name, inputs, expected) — expected = doc-06 §6.5 hand-computed constants.
CASES = [
    ("1", dict(P=4600, U=230, ph=1, pf=1.0, ins="PVC", amb=35, grp=1, L=25, dev="MCB", iscc=800),
     dict(IB=20.00, In=20, S=4, Iz=27.00, vd=2.45, sadia=2.11, gov="overload_coordination")),
    ("2", dict(P=15000, U=400, ph=3, pf=0.85, ins="XLPE", amb=40, grp=3, L=50, dev="gG_fuse", iscc=2500),
     dict(IB=25.47, In=32, S=10, Iz=44.55, vd=1.08, sadia=5.27, gov="overload_coordination")),
    ("2b", dict(P=15000, U=400, ph=3, pf=0.85, ins="XLPE", amb=40, grp=3, L=50, dev="MCB", iscc=2500),
     dict(IB=25.47, In=32, S=6, Iz=32.40, vd=1.78, sadia=5.27, gov="overload_coordination")),
    ("3", dict(P=4600, U=230, ph=1, pf=1.0, ins="PVC", amb=30, grp=1, L=60, dev="MCB", iscc=800),
     dict(IB=20.00, In=20, S=6, Iz=40.00, vd=3.91, sadia=2.11, gov="voltage_drop")),
    ("4", dict(I=100, U=400, ph=3, pf=0.9, ins="XLPE", amb=30, grp=1, L=30, dev="MCB", iscc=20000, t=0.2),
     dict(IB=100.0, In=100, S=70, Iz=225.00, vd=0.42, sadia=59.63, gov="short_circuit")),
]


@pytest.mark.parametrize("name,inp,exp", CASES, ids=[c[0] for c in CASES])
def test_golden_case(name, inp, exp):
    r = size(make_request(**inp))
    assert r.overall_status == "PASS", name
    assert r.selected_cable.cross_section_mm2 == exp["S"]
    assert r.selected_protection.In_a == exp["In"]
    assert r.selected_cable.governing_constraint == exp["gov"]
    assert r.design_current_a == pytest.approx(exp["IB"], abs=0.01)
    assert r.selected_cable.Iz_a == pytest.approx(exp["Iz"], abs=0.02)
    assert r.voltage_drop_pct == pytest.approx(exp["vd"], abs=0.02)
    assert r.adiabatic_min_mm2 == pytest.approx(exp["sadia"], abs=0.02)


def test_case2_fuse_forces_larger_than_mcb():
    """gG fuse (I2/In=1.6) binds harder than MCB (1.45): 10 mm² vs 6 mm²."""
    fuse = size(make_request(P=15000, U=400, ph=3, pf=0.85, ins="XLPE", amb=40, grp=3,
                             L=50, dev="gG_fuse", iscc=2500))
    mcb = size(make_request(P=15000, U=400, ph=3, pf=0.85, ins="XLPE", amb=40, grp=3,
                            L=50, dev="MCB", iscc=2500))
    assert fuse.selected_cable.cross_section_mm2 == 10
    assert mcb.selected_cable.cross_section_mm2 == 6


def test_every_step_has_citation():
    r = size(make_request(P=4600, U=230, ph=1, pf=1.0, ins="PVC", amb=35, grp=1, L=25,
                          dev="MCB", iscc=800))
    for step in r.audit_trace:
        assert step.citations, f"step {step.id} lacks citation"


# --- pue-rk (public_standard, docs/12 §1.1-1.2, docs/04 §4.6) ---------------------------
# Scope v1: methods C (открыто) + B1 (в трубе), Cu+Al, PVC. B1/Al cover only part of the
# section series in the source; the engine sweeps the covered sections (pack.has_ampacity)
# instead of crashing on the first gap. Expected values were independently recomputed via
# `scripts/reference_calc.py pue-rk` (docs/12 §1.2) — NOT copied from the engine's output.
PUE_RK_CASES = [
    ("C-Cu-overload", dict(method="C", material="Cu", P=4000, U=230, ph=1, pf=1.0, ins="PVC", amb=35, grp=1, L=20, dev="MCB"),
     dict(IB=17.39, In=20, S=1.5, Iz=20.01, vd=4.54, sadia=None, gov="overload_coordination")),
    ("C-Cu-fuse", dict(method="C", material="Cu", P=15000, U=400, ph=3, pf=0.85, ins="PVC", amb=40, grp=5, L=50, dev="gG_fuse", iscc=2500),
     dict(IB=25.47, In=32, S=10, Iz=42.98, vd=1.08, sadia=6.88, gov="overload_coordination")),
    ("C-Cu-vd", dict(method="C", material="Cu", P=3500, U=230, ph=1, pf=1.0, ins="PVC", amb=25, grp=1, L=80, dev="MCB", iscc=800),
     dict(IB=15.22, In=16, S=6, Iz=50.00, vd=3.97, sadia=2.20, gov="voltage_drop")),
    ("C-Cu-sc", dict(method="C", material="Cu", I=90, U=400, ph=3, pf=0.9, ins="PVC", amb=25, grp=1, L=20, dev="MCB", iscc=18000, t=0.2),
     dict(IB=90.00, In=100, S=70, Iz=270.00, vd=0.25, sadia=70.00, gov="short_circuit")),
    ("B1-Cu", dict(method="B1", material="Cu", P=3500, U=230, ph=1, pf=1.0, ins="PVC", amb=25, grp=1, L=30, dev="MCB", iscc=800),
     dict(IB=15.22, In=16, S=2.5, Iz=25.00, vd=3.57, sadia=2.20, gov="short_circuit")),
    ("C-Al-fuse", dict(method="C", material="Al", P=15000, U=400, ph=3, pf=0.85, ins="PVC", amb=40, grp=3, L=50, dev="gG_fuse", iscc=2500),
     dict(IB=25.47, In=32, S=16, Iz=59.25, vd=1.08, sadia=10.40, gov="short_circuit")),
    ("B1-Al", dict(method="B1", material="Al", P=6000, U=230, ph=1, pf=1.0, ins="PVC", amb=25, grp=1, L=20, dev="MCB", iscc=1000),
     dict(IB=26.09, In=32, S=10, Iz=42.00, vd=1.63, sadia=4.16, gov="overload_coordination")),
]


@pytest.mark.parametrize("name,inp,exp", PUE_RK_CASES, ids=[c[0] for c in PUE_RK_CASES])
def test_golden_case_pue_rk(name, inp, exp):
    pack = load_data_pack("pue-rk")
    r = size(make_request(**inp), data_pack=pack)
    assert r.overall_status == "PASS", name
    assert r.data_pack.status == "public_standard"
    assert r.selected_cable.cross_section_mm2 == exp["S"]
    assert r.selected_protection.In_a == exp["In"]
    assert r.selected_cable.governing_constraint == exp["gov"]
    assert r.design_current_a == pytest.approx(exp["IB"], abs=0.01)
    assert r.selected_cable.Iz_a == pytest.approx(exp["Iz"], abs=0.02)
    assert r.voltage_drop_pct == pytest.approx(exp["vd"], abs=0.02)
    if exp["sadia"] is None:
        assert r.adiabatic_min_mm2 is None
    else:
        assert r.adiabatic_min_mm2 == pytest.approx(exp["sadia"], abs=0.02)


def test_pue_rk_uncovered_method_errors():
    """A method/material/insulation the pack doesn't cover at all raises a clean DataPackError
    (honest off-table, docs/12 §1.1) — NOT a silent wrong answer. pue-rk has no XLPE/method-D."""
    pack = load_data_pack("pue-rk")
    with pytest.raises(DataPackError, match="no ampacity data"):
        size(make_request(method="C", material="Cu", ins="XLPE", P=4000, U=230, ph=1, pf=1.0,
                          amb=25, grp=1, L=20, dev="MCB"), data_pack=pack)


def test_pue_rk_ambient_off_table_still_errors():
    """Section-off-table is skipped, but ambient-off-table must STILL hard-error (it's a real
    input the pack can't honor) — the sweep's has_ampacity gate must not swallow it."""
    pack = load_data_pack("pue-rk")
    with pytest.raises(DataPackError, match="ambient"):
        size(make_request(method="C", material="Cu", ins="PVC", P=4000, U=230, ph=1, pf=1.0,
                          amb=33, grp=1, L=20, dev="MCB"), data_pack=pack)


def test_pue_rk_partial_coverage_fails_gracefully():
    """When even the largest COVERED section can't satisfy the load (B1 tops out at 120 mm²),
    the result is a clean FAIL at that section, not a crash on an uncovered larger section."""
    pack = load_data_pack("pue-rk")
    r = size(make_request(method="B1", material="Cu", ins="PVC", I=260, U=400, ph=3, pf=0.9,
                          amb=25, grp=1, L=10, dev="MCB", iscc=5000), data_pack=pack)
    assert r.overall_status == "FAIL"
    assert r.selected_cable.cross_section_mm2 == 120  # largest B1/Cu section pue-rk covers
