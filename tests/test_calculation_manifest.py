"""PER-22: deterministic calculation identity across product surfaces."""
from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy

import pytest
from pydantic import ValidationError

from electricopilot.calculation_manifest import (
    CalculationManifest,
    build_calculation_manifest,
    sha256_hex,
    verify_calculation_manifest,
)
from electricopilot.data.loader import load_data_pack
from electricopilot.export import build_bundle
from electricopilot.import_schedule import build_import_diff, import_schedule
from electricopilot.normcheck import build_normcheck_report
from electricopilot.project import build_project_report


def _project() -> dict:
    return {
        "schema_version": 2,
        "id": "project-manifest",
        "name": "Щит identity",
        "board_ref": "ID-1",
        "location": "Алматы",
        "supply": {"voltage_v": 400, "phases": 3, "ways_total": 12},
        "diversity": {"factors": {"socket": 0.5}},
        "circuits": [{
            "id": "c1",
            "ref": "QF1",
            "sort_index": 0,
            "request": {
                "load": {
                    "description": "Розетки",
                    "power_w": 3680,
                    "voltage_v": 230,
                    "phases": 1,
                    "power_factor": 0.95,
                    "purpose": "socket",
                },
                "installation": {
                    "method": "C",
                    "material": "Cu",
                    "insulation": "PVC",
                    "ambient_temp_c": 30,
                    "grouping_circuits": 1,
                    "length_m": 18,
                },
                "protection": {
                    "device_class": "MCB",
                    "prospective_fault_current_a": 1500,
                    "disconnection_time_s": 0.1,
                    "trip_curve_type": "C",
                },
            },
            "meta": {"phase": "L1", "rcd": {"present": True, "type": "RCD", "ma": 30}},
            "result": {"status": "STALE", "section": 999},
        }],
    }


def test_manifest_is_stable_and_ignores_untrusted_ui_snapshot() -> None:
    pack = load_data_pack()
    first = build_calculation_manifest(_project(), pack, build_identity="build-a")
    second = build_calculation_manifest(_project(), pack, build_identity="build-a")
    stale_changed = deepcopy(_project())
    stale_changed["circuits"][0]["result"] = {"status": "OTHER", "section": 1.5}
    stale = build_calculation_manifest(stale_changed, pack, build_identity="build-a")

    assert first == second == stale
    assert first.calculation_id.startswith("calc-v1-")
    assert CalculationManifest.model_validate_json(first.model_dump_json()) == first


def test_manifest_marks_missing_build_and_rejects_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ELECTRICOPILOT_BUILD_IDENTITY", raising=False)
    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA", raising=False)
    manifest = build_calculation_manifest(_project(), load_data_pack())
    assert manifest.application.build.status == "unavailable"
    assert manifest.application.build.value is None

    tampered = manifest.model_dump(mode="json")
    tampered["project_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="manifest_sha256 does not match"):
        CalculationManifest.model_validate(tampered)


def test_manifest_changes_for_effective_input_pack_content_and_build() -> None:
    project = _project()
    pack = load_data_pack()
    baseline = build_calculation_manifest(project, pack, build_identity="build-a")

    changed_project = deepcopy(project)
    changed_project["circuits"][0]["request"]["installation"]["length_m"] = 19
    project_manifest = build_calculation_manifest(changed_project, pack, build_identity="build-a")

    changed_pack = pack.model_copy(update={
        "provenance": {
            **pack.provenance,
            "normcheck.R01": pack.provenance["normcheck.R01"].model_copy(
                update={"source_document": "same-version-different-content"},
            ),
        },
    })
    pack_manifest = build_calculation_manifest(project, changed_pack, build_identity="build-a")
    build_manifest = build_calculation_manifest(project, pack, build_identity="build-b")

    assert project_manifest.project_sha256 != baseline.project_sha256
    assert changed_pack.meta.name == pack.meta.name and changed_pack.meta.version == pack.meta.version
    assert pack_manifest.norm_pack.sha256 != baseline.norm_pack.sha256
    assert len({
        baseline.calculation_id,
        project_manifest.calculation_id,
        pack_manifest.calculation_id,
        build_manifest.calculation_id,
    }) == 4


def test_report_normcheck_and_r11_share_one_manifest() -> None:
    csv_data = (
        "Ref;Описание;P, кВт;U, В;Фазы;cos φ;Длина, м;Аппарат;In, А;Кривая;"
        "Сечение, мм²;Материал;Изоляция;Метод\n"
        "C1;Розетки;3,68;230;1;0,95;18;MCB;20;C;2,5;Cu;PVC;C\n"
    ).encode("utf-8")
    pack = load_data_pack()
    imported = import_schedule(filename="board.csv", data=csv_data, pack=pack)
    project = imported.project_draft

    report = build_project_report(project, data_pack=pack)
    normcheck = build_normcheck_report(project, pack)
    diff = build_import_diff(project, pack)

    assert report["calculation_id"] == normcheck.calculation_id == diff.calculation_id
    assert report["calculation_manifest"] == normcheck.calculation_manifest.model_dump(mode="json")
    assert normcheck.calculation_manifest == diff.calculation_manifest
    assert report["calculation_id"] in report["markdown"]
    assert "NEEDS_REVIEW" in diff.data_identity


def test_bundle_manifest_verifies_canonical_machine_inputs() -> None:
    archive = zipfile.ZipFile(io.BytesIO(build_bundle(_project())))
    names = set(archive.namelist())
    assert {"manifest.json", "calculation-input.json", "norm-pack.json"} <= names

    manifest = CalculationManifest.model_validate_json(archive.read("manifest.json"))
    assert sha256_hex(archive.read("calculation-input.json")) == manifest.project_sha256
    assert sha256_hex(archive.read("norm-pack.json")) == manifest.norm_pack.sha256
    assert manifest.calculation_id.encode() in archive.read("report.md")
    assert manifest.calculation_id.encode() in archive.read("sld.svg")

    project = json.loads(archive.read("project.json"))
    verified = verify_calculation_manifest(manifest, project, load_data_pack())
    assert verified.ok and verified.mismatches == []

    changed = deepcopy(project)
    changed["circuits"][0]["request"]["load"]["power_w"] = 4000
    mismatch = verify_calculation_manifest(manifest, changed, load_data_pack())
    assert mismatch.ok is False
    assert "project_sha256" in mismatch.mismatches
