"""Deterministic R01-R10 normcheck: rules, trust gates, ordering and API (docs/14)."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi.testclient import TestClient

from electricopilot.data.loader import DataPack, load_data_pack
from electricopilot.normcheck import (
    RULES,
    Finding,
    build_normcheck_report,
    narrate_findings,
    run_normcheck,
)
from electricopilot.studio_api import app


def _circuit(
    ref: str = "C1",
    *,
    purpose: str = "power",
    power_w: float = 2000,
    phase: str = "L1",
    material: str = "Cu",
    curve: str = "C",
    iscc: float | None = 1500,
    length_m: float = 20,
    rcd: bool = False,
    rcd_ma: float = 30,
    pe_section_mm2: float | None = 1.5,
    max_vd_pct: float | None = None,
    phases: int = 1,
    voltage_v: float = 230,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "phase": phase,
        "rcd": ({"present": True, "type": "RCBO", "ma": rcd_ma}
                if rcd else {"present": False}),
    }
    if pe_section_mm2 is not None:
        meta["pe_section_mm2"] = pe_section_mm2
    return {
        "id": f"id-{ref}",
        "ref": ref,
        "request": {
            "load": {
                "description": ref,
                "power_w": power_w,
                "voltage_v": voltage_v,
                "phases": phases,
                "power_factor": 1.0,
                "purpose": purpose,
            },
            "installation": {
                "method": "C",
                "material": material,
                "insulation": "PVC",
                "ambient_temp_c": 30 if material == "Cu" else 25,
                "grouping_circuits": 1,
                "length_m": length_m,
            },
            "protection": {
                "device_class": "MCB",
                "prospective_fault_current_a": iscc,
                "disconnection_time_s": 0.1,
                "max_voltage_drop_pct": max_vd_pct,
                "trip_curve_type": curve,
            },
        },
        "meta": meta,
        "result": {"status": "STALE_CLIENT_VALUE_MUST_BE_IGNORED"},
    }


def _project(*circuits: dict[str, Any], ways_total: int = 12) -> dict[str, Any]:
    return {
        "name": "Normcheck fixture",
        "board_ref": "NC-1",
        "supply": {"voltage_v": 400, "phases": 3, "ways_total": ways_total},
        "circuits": list(circuits),
    }


def _for(findings: list[Finding], rule_id: str) -> list[Finding]:
    return [finding for finding in findings if finding.rule_id == rule_id]


def _violations(project: dict[str, Any], pack: DataPack, rule_id: str) -> list[Finding]:
    return [finding for finding in _for(run_normcheck(project, pack), rule_id)
            if finding.status == "violation"]


def test_registry_contains_r01_to_r10() -> None:
    assert sorted(RULES) == [f"R{i:02}" for i in range(1, 11)]


def test_r01_failed_circuit_positive_and_negative(verified_pack: DataPack) -> None:
    failed = _project(_circuit(length_m=200, max_vd_pct=0.001))
    assert _violations(failed, verified_pack, "R01")[0].observed["overall_status"] == "FAIL"
    assert _violations(_project(_circuit()), verified_pack, "R01") == []


def test_r02_socket_rcd_positive_negative_and_stale_result_ignored(
    verified_pack: DataPack,
) -> None:
    bad = _project(_circuit(purpose="socket", rcd=False))
    finding = _violations(bad, verified_pack, "R02")[0]
    assert finding.circuit_ref == "C1" and finding.observed["rcd_present"] is False
    assert _violations(_project(_circuit(purpose="socket", rcd=True)), verified_pack, "R02") == []


def test_r02_invalid_rcd_setting_is_not_checked(verified_pack: DataPack) -> None:
    project = _project(_circuit(purpose="socket", rcd=True))
    project["circuits"][0]["meta"]["rcd"]["ma"] = "not-a-number"
    finding = _for(run_normcheck(project, verified_pack), "R02")[0]
    assert finding.status == "not_checked" and finding.reason == "invalid_input"


def test_r03_cumulative_voltage_drop_positive_and_negative(verified_pack: DataPack) -> None:
    bad = _project(_circuit(length_m=40))
    bad["supply"]["feeder"] = {"length_m": 500, "section_mm2": 1.5, "material": "Cu"}
    assert _violations(bad, verified_pack, "R03")[0].observed["total_vd_pct"] > 5
    good = _project(_circuit(length_m=5))
    good["supply"]["feeder"] = {"length_m": 1, "section_mm2": 300, "material": "Cu"}
    assert _violations(good, verified_pack, "R03") == []


def test_r03_missing_feeder_is_not_checked(verified_pack: DataPack) -> None:
    finding = _for(run_normcheck(_project(_circuit()), verified_pack), "R03")[0]
    assert finding.status == "not_checked" and finding.reason == "missing_input"


def test_r04_incomer_nominal_positive_negative_and_missing(verified_pack: DataPack) -> None:
    bad = _project(_circuit(power_w=3000))
    bad["supply"]["incomer"] = {"device_class": "MCB", "In_a": 10}
    assert _violations(bad, verified_pack, "R04")
    good = _project(_circuit(power_w=3000))
    good["supply"]["incomer"] = {"device_class": "MCB", "In_a": 63}
    assert _violations(good, verified_pack, "R04") == []
    missing = _for(run_normcheck(_project(_circuit()), verified_pack), "R04")[0]
    assert missing.status == "not_checked" and missing.reason == "missing_input"


def test_r05_phase_imbalance_positive_and_negative(verified_pack: DataPack) -> None:
    assert _violations(_project(_circuit(phase="L1")), verified_pack, "R05")
    balanced = _project(
        _circuit("C1", phase="L1"),
        _circuit("C2", phase="L2"),
        _circuit("C3", phase="L3"),
    )
    assert _violations(balanced, verified_pack, "R05") == []


def test_r05_threshold_comes_from_pack(verified_pack: DataPack) -> None:
    project = _project(_circuit(phase="L1"))
    assert _violations(project, verified_pack, "R05")
    config = deepcopy(verified_pack.normcheck)
    config["R05"]["max_imbalance_pct"] = 400
    changed = verified_pack.model_copy(update={"normcheck": config})
    assert _violations(project, changed, "R05") == []


def test_r06_board_spare_positive_and_negative(verified_pack: DataPack) -> None:
    assert _violations(_project(_circuit(), ways_total=1), verified_pack, "R06")
    assert _violations(_project(_circuit(), ways_total=12), verified_pack, "R06") == []


def test_r07_motor_curve_positive_and_negative(verified_pack: DataPack) -> None:
    assert _violations(_project(_circuit(purpose="motor", curve="B")), verified_pack, "R07")
    assert _violations(_project(_circuit(purpose="motor", curve="C")), verified_pack, "R07") == []


def test_r08_aluminium_minimum_positive_and_negative(
    verified_public_pack: DataPack,
) -> None:
    pack = verified_public_pack.model_copy(update={"normcheck": load_data_pack().normcheck})
    assert _violations(_project(_circuit(material="Al", power_w=1000)), pack, "R08")
    assert _violations(_project(_circuit(material="Al", power_w=20000)), pack, "R08") == []


def test_r09_missing_iscc_positive_and_negative(verified_pack: DataPack) -> None:
    assert _violations(_project(_circuit(iscc=None)), verified_pack, "R09")
    assert _violations(_project(_circuit(iscc=1500)), verified_pack, "R09") == []


def test_r10_pe_table_positive_negative_and_missing(verified_pack: DataPack) -> None:
    assert _violations(_project(_circuit(pe_section_mm2=1.0)), verified_pack, "R10")
    assert _violations(_project(_circuit(pe_section_mm2=4.0)), verified_pack, "R10") == []
    missing = _for(run_normcheck(
        _project(_circuit(pe_section_mm2=None)), verified_pack,
    ), "R10")[0]
    assert missing.status == "not_checked" and missing.reason == "missing_input"


def test_untrusted_stub_downgrades_candidates_instead_of_false_violation() -> None:
    findings = run_normcheck(_project(_circuit(purpose="socket", rcd=False)), load_data_pack())
    assert len(findings) == 10
    assert all(finding.status == "not_checked" for finding in findings)
    assert _for(findings, "R02")[0].observed["rcd_present"] is False


def test_pue_rk_has_explicit_not_configured_findings_not_false_pass() -> None:
    findings = run_normcheck(_project(_circuit()), load_data_pack("pue-rk"))
    assert len(findings) == 10
    assert {finding.reason for finding in findings} == {"rule_not_configured"}


def test_stable_sort_is_severity_then_ref_then_rule(verified_pack: DataPack) -> None:
    project = _project(
        _circuit("Z2", purpose="socket", rcd=False, iscc=None, pe_section_mm2=None),
        _circuit("A1", purpose="motor", curve="B", iscc=None, pe_section_mm2=1.0),
        ways_total=2,
    )
    findings = run_normcheck(project, verified_pack)
    rank = {"error": 0, "warning": 1, "info": 2}
    keys = [(rank[f.severity], f.circuit_ref or "", f.rule_id, f.status) for f in findings]
    assert keys == sorted(keys)


def test_summary_counts_violations_separately_from_not_checked(verified_pack: DataPack) -> None:
    report = build_normcheck_report(
        _project(_circuit(purpose="socket", rcd=False, iscc=None, pe_section_mm2=None)),
        verified_pack,
    )
    assert report.summary.errors >= 1
    assert report.summary.warnings >= 1
    assert report.summary.not_checked >= 1
    assert report.summary.total == len(report.findings)


def test_invalid_optional_board_inputs_are_not_checked_not_server_errors(
    verified_pack: DataPack,
) -> None:
    project = _project(_circuit(pe_section_mm2=None))
    project["circuits"][0]["meta"]["pe_section_mm2"] = "not-a-number"
    project["supply"]["feeder"] = {
        "length_m": 10, "section_mm2": 0, "material": "Cu",
    }
    project["supply"]["incomer"] = {"In_a": "not-a-number"}
    findings = run_normcheck(project, verified_pack)
    assert _for(findings, "R03")[0].status == "not_checked"
    assert _for(findings, "R04")[0].reason == "invalid_input"
    assert _for(findings, "R10")[0].reason == "invalid_input"


def test_api_available_without_llm_key(monkeypatch: Any) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    response = TestClient(app).post("/api/normcheck", json={"project": _project(_circuit())})
    assert response.status_code == 200
    payload = response.json()
    assert payload["narrative"] is None
    assert payload["summary"]["not_checked"] == 10
    assert payload["norm_pack"]["name"] == "iec-stub"
    assert "UNSIGNED_ADVISORY" in payload["signoff_notice"]


class _NarrativeClient:
    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, **_: Any) -> str:
        return self.text


def test_llm_narrative_cannot_change_findings_or_add_number(verified_pack: DataPack) -> None:
    report = build_normcheck_report(
        _project(_circuit(purpose="socket", rcd=False)), verified_pack,
    )
    before = [finding.model_dump() for finding in report.findings]
    clean = narrate_findings(
        report.findings, report.summary,
        _NarrativeClient("Требуется инженерная проверка findings R02."), model="fake",
    )
    assert clean is not None and clean.provenance_ok
    rejected = narrate_findings(
        report.findings, report.summary,
        _NarrativeClient("Увеличьте сечение до 777 мм²."), model="fake",
    )
    assert rejected is None
    assert [finding.model_dump() for finding in report.findings] == before
