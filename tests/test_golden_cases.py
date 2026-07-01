"""Golden domain cases — assert the LITERAL doc-06 §6.5 constants (not reference_calc)."""
from __future__ import annotations

import pytest

from electricopilot.engine import size

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
