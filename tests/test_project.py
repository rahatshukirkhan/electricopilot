"""Project (board) aggregation — panel schedule, totals, phase balance (docs/11)."""
from __future__ import annotations

from electricopilot.project import build_project_report


def _c(cid, ref, phase, **req_over):
    load = {"description": ref, "power_w": 3680, "voltage_v": 230, "phases": 1,
            "power_factor": 0.95, "purpose": "socket"}
    load.update(req_over.get("load", {}))
    return {"id": cid, "ref": ref, "meta": {"phase": phase, "diversity_category": load["purpose"]},
            "request": {"load": load,
                        "installation": {"method": "C", "material": "Cu", "insulation": "PVC",
                                         "ambient_temp_c": 30, "grouping_circuits": 1, "length_m": 18},
                        "protection": {"device_class": "MCB", "prospective_fault_current_a": 1500,
                                       "disconnection_time_s": 0.01, "trip_curve_type": "C"}}}


PROJECT = {
    "name": "Щит", "board_ref": "DB-1", "supply": {"voltage_v": 400, "ways_total": 12},
    "diversity": {"factors": {"socket": 0.5, "motor": 1.0}},
    "circuits": [
        _c("c1", "L1", "L1"),
        _c("c2", "L2", "L2"),
        _c("c3", "M1", "L1L2L3",
           load={"description": "Двигатель", "power_w": 15000, "voltage_v": 400, "phases": 3,
                 "power_factor": 0.85, "purpose": "motor"}),
    ],
}


def test_report_shape_and_status():
    r = build_project_report(PROJECT)
    assert set(r) >= {"board", "rows", "markdown", "provenance_note", "disclaimer"}
    assert len(r["rows"]) == 3
    assert r["board"]["status"] in ("PASS", "FAIL", "NEEDS_REVIEW")
    assert r["provenance_note"] and r["disclaimer"]
    assert "Таблица щита" in r["markdown"]


def test_phase_balance_is_real_arithmetic():
    r = build_project_report(PROJECT)
    ph = r["board"]["totals"]["phase"]
    # single-phase c1 on L1 (~16.8A), c2 on L2 (~16.8A); 3ph motor (~25.5A) adds to ALL phases
    assert ph["L1"]["A"] > ph["L3"]["A"]           # L1 carries socket + motor; L3 only motor
    assert ph["L3"]["A"] > 20                        # motor contribution present on L3
    assert r["board"]["totals"]["imbalance_pct"] > 0


def test_totals_and_ways():
    r = build_project_report(PROJECT)
    t = r["board"]["totals"]
    assert t["connected_kw"] > 20                    # 3.68+3.68+15 ≈ 22.4 kW
    assert r["board"]["ways"]["used"] == 3 and r["board"]["ways"]["total"] == 12
    assert r["board"]["demand"]["provenance"] == "illustrative"
