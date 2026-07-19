"""Versioned board-project contract, migration, and API boundary regressions (docs/11)."""
from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from electricopilot import studio_api
from electricopilot.exceptions import ProjectContractError
from electricopilot.export import build_bundle
from electricopilot.project_contract import project_payload, validate_project
from electricopilot.studio_api import app


def _request() -> dict[str, Any]:
    return {
        "load": {
            "description": "Розетки",
            "power_w": 2000,
            "voltage_v": 230,
            "phases": 1,
            "power_factor": 0.9,
            "purpose": "socket",
        },
        "installation": {
            "method": "C",
            "material": "Cu",
            "insulation": "PVC",
            "ambient_temp_c": 30,
            "grouping_circuits": 1,
            "length_m": 20,
        },
        "protection": {
            "device_class": "MCB",
            "prospective_fault_current_a": 1500,
            "disconnection_time_s": 0.1,
            "trip_curve_type": "C",
        },
    }


def _project() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "id": "project-contract",
        "name": "Контрактный щит",
        "board_ref": "DB-C",
        "location": "Электрощитовая",
        "created_at": "2026-07-19T00:00:00Z",
        "updated_at": "2026-07-19T00:00:00Z",
        "norm_pack": None,
        "supply": {
            "voltage_v": 400,
            "phases": 3,
            "ways_total": 12,
            "earthing": "TN-C-S",
            "method": "C",
            "material": "Cu",
            "insulation": "PVC",
            "ambient_temp_c": 30,
        },
        "diversity": {"factors": {"socket": 0.5}},
        "export_settings": {"cable_margin": 1.05},
        "circuits": [{
            "id": "circuit-1",
            "ref": "C1",
            "sort_index": 0,
            "request": _request(),
            "meta": {
                "phase": "L1",
                "rcd": {"present": True, "type": "RCBO", "ma": 30},
                "diversity_category": "socket",
                "cores": "1P+N",
                "pe_section_mm2": 2.5,
            },
            "result": {
                "status": "STALE_CLIENT_VALUE_MUST_BE_IGNORED",
                "section": None,
                "In": None,
                "IB": None,
                "Iz": None,
                "vd": None,
                "governing": None,
            },
            "signoff": {"status": "UNSIGNED_ADVISORY", "ack_illustrative": False},
        }],
    }


def test_current_project_round_trips_through_one_canonical_model() -> None:
    source = _project()
    canonical = validate_project(source)
    serialized = project_payload(canonical)

    assert validate_project(serialized) == canonical
    assert serialized["schema_version"] == 2
    request = serialized["circuits"][0]["request"]
    assert request["load"]["power_w"] == source["circuits"][0]["request"]["load"]["power_w"]
    assert request["installation"]["length_m"] == 20
    assert serialized["circuits"][0]["result"]["status"].startswith("STALE_CLIENT")
    assert serialized["circuits"][0]["signoff"]["status"] == "UNSIGNED_ADVISORY"


def test_unversioned_legacy_migration_is_pure_and_deterministic() -> None:
    legacy = _project()
    legacy.pop("schema_version")
    legacy["supply"].pop("phases")
    legacy["supply"].pop("voltage_v")
    legacy["circuits"][0].pop("sort_index")
    before = deepcopy(legacy)

    first = project_payload(legacy)
    second = project_payload(legacy)

    assert legacy == before
    assert first == second
    assert first["schema_version"] == 2
    assert first["supply"]["phases"] == 3 and first["supply"]["voltage_v"] == 400
    assert first["circuits"][0]["sort_index"] == 0


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda p: p.update({"schema_version": 99}), "schema_version"),
        (lambda p: p["supply"].update({"mystery": True}), "supply.mystery"),
        (lambda p: p.pop("name"), "name"),
        (
            lambda p: p["circuits"].append(deepcopy(p["circuits"][0])),
            "circuits",
        ),
    ],
)
def test_future_unknown_missing_and_duplicate_inputs_are_rejected(
    mutate: Any,
    path: str,
) -> None:
    project = _project()
    mutate(project)
    with pytest.raises(ProjectContractError) as caught:
        validate_project(project)
    assert path in caught.value.path


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/project-validate",
        "/api/project-report",
        "/api/normcheck",
        "/api/project-sld",
        "/api/project-export",
    ],
)
def test_every_project_api_rejects_invalid_contract_with_stable_path(
    endpoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_downstream(*_: Any, **__: Any) -> Any:
        pytest.fail("invalid project reached a downstream calculation/export function")

    monkeypatch.setattr(studio_api, "_report_for", unexpected_downstream)
    monkeypatch.setattr(studio_api, "build_normcheck_report", unexpected_downstream)
    monkeypatch.setattr(studio_api, "build_bundle", unexpected_downstream)
    project = _project()
    project["supply"]["unexpected"] = "not silently ignored"

    response = TestClient(app).post(endpoint, json={"project": project})

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_project_contract",
        "path": "project.supply.unexpected",
        "message": "Некорректный проект: Extra inputs are not permitted",
    }


def test_validation_endpoint_returns_canonical_legacy_project_without_sizing() -> None:
    legacy = _project()
    legacy.pop("schema_version")
    legacy["circuits"][0].pop("sort_index")

    response = TestClient(app).post("/api/project-validate", json={"project": legacy})

    assert response.status_code == 200
    canonical = response.json()["project"]
    assert canonical["schema_version"] == 2
    assert canonical["circuits"][0]["sort_index"] == 0
    assert canonical["circuits"][0]["result"]["status"].startswith("STALE_CLIENT")


def test_bundle_embeds_canonical_schema_v2_project() -> None:
    legacy = _project()
    legacy.pop("schema_version")
    legacy["circuits"][0].pop("sort_index")

    archive = zipfile.ZipFile(io.BytesIO(build_bundle(legacy)))
    embedded = json.loads(archive.read("project.json"))

    assert embedded == project_payload(legacy)
    assert "UNSIGNED_ADVISORY" in archive.read("report.md").decode("utf-8")


def test_studio_import_validates_before_localstorage_write() -> None:
    app_js = (Path(__file__).parents[1] / "web/app.js").read_text(encoding="utf-8")
    validate_call = "postJSON('/api/project-validate', { project: p })"
    assert validate_call in app_js
    assert app_js.index(validate_call) < app_js.index("projSet(copy)", app_js.index(validate_call))
