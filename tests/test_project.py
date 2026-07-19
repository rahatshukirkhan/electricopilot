"""Project (board) aggregation — panel schedule, totals, phase balance (docs/11)."""
from __future__ import annotations

import math
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from electricopilot.exceptions import ProjectContractError, ProjectTopologyError
from electricopilot.project import build_project_report
from electricopilot.studio_api import app


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
    "id": "project-main", "name": "Щит", "board_ref": "DB-1",
    "supply": {"voltage_v": 400, "ways_total": 12},
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


def _one_phase_project() -> dict:
    return {
        "id": "project-one-phase",
        "name": "1ф щит",
        "board_ref": "DB-1F",
        "supply": {"voltage_v": 230, "phases": 1, "ways_total": 6},
        "diversity": {"factors": {"socket": 0.5}},
        "circuits": [_c("c1", "L1", "L1")],
    }


def test_one_and_three_phase_board_formulae_use_explicit_topology():
    one_phase = _one_phase_project()
    three_phase = deepcopy(one_phase)
    three_phase["supply"].update({"voltage_v": 400, "phases": 3})

    one = build_project_report(one_phase)
    three = build_project_report(three_phase)

    one_totals = one["board"]["totals"]
    assert one["board"]["topology"] == {"voltage_v": 230.0, "phases": 1}
    assert one_totals["phase"]["L1"]["A"] == one["rows"][0]["IB_a"]
    assert one_totals["phase"]["L2"]["A"] == one_totals["phase"]["L3"]["A"] == 0
    assert one_totals["imbalance_pct"] is None
    assert one_totals["imbalance_applicable"] is False
    expected_one_a = one["board"]["demand"]["emd_kva"] * 1000 / 230
    assert one["board"]["demand"]["incomer_md_a"] == pytest.approx(expected_one_a, abs=0.1)
    assert "перекос фаз неприменим" in one["markdown"]

    three_totals = three["board"]["totals"]
    assert three["board"]["topology"] == {"voltage_v": 400.0, "phases": 3}
    assert three_totals["imbalance_pct"] == pytest.approx(300.0)
    assert three_totals["imbalance_applicable"] is True
    expected_three_a = three["board"]["demand"]["emd_kva"] * 1000 / (math.sqrt(3) * 400)
    assert three["board"]["demand"]["incomer_md_a"] == pytest.approx(expected_three_a, abs=0.1)


def test_invalid_topology_is_rejected_before_partial_calculation():
    invalid_projects = []

    invalid_phases = _one_phase_project()
    invalid_phases["supply"]["phases"] = 2
    invalid_projects.append(invalid_phases)

    string_phases = _one_phase_project()
    string_phases["supply"]["phases"] = "1"
    invalid_projects.append(string_phases)

    null_supply = _one_phase_project()
    null_supply["supply"] = None
    invalid_projects.append(null_supply)

    wrong_phase = _one_phase_project()
    wrong_phase["circuits"][0]["meta"]["phase"] = "L2"
    invalid_projects.append(wrong_phase)

    wrong_voltage = _one_phase_project()
    wrong_voltage["circuits"][0]["request"]["load"]["voltage_v"] = 400
    invalid_projects.append(wrong_voltage)

    three_phase_circuit = _one_phase_project()
    three_phase_circuit["circuits"][0]["request"]["load"].update({
        "phases": 3, "voltage_v": 230,
    })
    three_phase_circuit["circuits"][0]["meta"]["phase"] = "L1L2L3"
    invalid_projects.append(three_phase_circuit)

    wrong_three_phase_voltage = _one_phase_project()
    wrong_three_phase_voltage["supply"].update({"voltage_v": 400, "phases": 3})
    wrong_three_phase_voltage["circuits"][0]["request"]["load"].update({
        "phases": 3, "voltage_v": 230,
    })
    wrong_three_phase_voltage["circuits"][0]["meta"]["phase"] = "L1L2L3"
    invalid_projects.append(wrong_three_phase_voltage)

    for project in invalid_projects:
        with pytest.raises((ProjectContractError, ProjectTopologyError)):
            build_project_report(project)


def test_project_endpoints_return_typed_422_for_invalid_topology():
    project = _one_phase_project()
    project["circuits"][0]["meta"]["phase"] = "L2"
    client = TestClient(app)

    for endpoint in ("/api/project-report", "/api/normcheck", "/api/project-export"):
        response = client.post(endpoint, json={"project": project})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_project_topology"
        assert "L1" in response.json()["detail"]["message"]
