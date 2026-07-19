"""Phase 3b schedule import: safe parsers, mapping boundary, project provenance and R11."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from electricopilot.data.loader import DataPack, load_data_pack
from electricopilot.import_schedule import (
    MAX_IMPORT_BYTES,
    build_import_diff,
    import_schedule,
    parse_schedule,
)
from electricopilot.project_contract import validate_project
from electricopilot.studio_api import app


HEADERS = [
    "Ref", "Описание", "P, кВт", "U, В", "Фазы", "cos φ", "Длина, м",
    "Аппарат", "In, А", "Кривая", "УЗО, мА", "Сечение, мм²", "Материал",
    "Изоляция", "Метод",
]
GOOD_ROW: list[Any] = [
    "C1", "Розетки", 3.68, 230, 1, 0.95, 18, "MCB", 20, "C", 30, 2.5,
    "Cu", "PVC", "C",
]


def _xlsx_bytes(headers: list[Any] = HEADERS, rows: list[list[Any]] | None = None) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Щит"
    sheet.append(headers)
    for row in rows or [GOOD_ROW]:
        sheet.append(row)
    target = io.BytesIO()
    workbook.save(target)
    return target.getvalue()


def _csv_bytes(headers: list[str] = HEADERS, rows: list[list[Any]] | None = None) -> bytes:
    target = io.StringIO()
    target.write(";".join(headers) + "\n")
    for row in rows or [GOOD_ROW]:
        target.write(";".join(str(value).replace(".", ",") if isinstance(value, float) else str(value)
                              for value in row) + "\n")
    return target.getvalue().encode("utf-8")


def test_clean_xlsx_fixture_maps_and_preserves_declared_values() -> None:
    parsed = parse_schedule("clean.xlsx", _xlsx_bytes())
    result = import_schedule(filename="clean.xlsx", data=_xlsx_bytes(), pack=load_data_pack())

    assert parsed.source_sheet == "Щит"
    mapped = {entry.source_header: entry.field for entry in result.mapping}
    assert mapped["P, кВт"] == "power_kw"
    assert mapped["Сечение, мм²"] == "declared_section_mm2"
    project = validate_project(result.project_draft)
    declaration = project.circuits[0].meta.import_declaration
    assert declaration is not None
    assert declaration.declared_section_mm2 == 2.5
    assert declaration.declared_in_a == 20
    assert declaration.raw_values["Описание"] == "Розетки"
    assert project.import_info is not None
    assert project.import_info.source_format == "xlsx"
    assert len(project.import_info.source_sha256) == 64
    assert any(issue.code == "assumed_project_defaults" for issue in result.issues)
    assert any(issue.code == "assumed_circuit_defaults" for issue in result.issues)


def test_russian_csv_fixture_uses_decimal_comma_and_deterministic_mapping() -> None:
    data = _csv_bytes()
    first = import_schedule(filename="щит.csv", data=data, pack=load_data_pack())
    second = import_schedule(filename="щит.csv", data=data, pack=load_data_pack())

    assert [(item.source_header, item.field, item.method) for item in first.mapping] == [
        (item.source_header, item.field, item.method) for item in second.mapping
    ]
    request = first.project_draft["circuits"][0]["request"]
    assert request["load"]["power_w"] == 3680
    assert request["load"]["power_factor"] == 0.95
    assert first.preview == second.preview


def test_missing_fields_fixture_marks_every_default_and_blocks_silent_acceptance() -> None:
    data = "Описание;P, кВт\nОсвещение;1,2\n".encode()
    result = import_schedule(filename="missing.csv", data=data, pack=load_data_pack())

    assumed = result.project_draft["circuits"][0]["meta"]["import_declaration"]["assumed_fields"]
    assert {"voltage_v", "phases", "length_m", "material", "insulation"} <= set(assumed)
    assert result.requires_confirmation is True
    assert result.assumptions_confirmed is False
    assert any(issue.code == "missing_declared_value" for issue in result.issues)
    assert result.diff.rows[0].status == "not_checked"


@pytest.mark.parametrize(
    ("filename", "data", "status", "code"),
    [
        ("schedule.txt", b"a,b\n1,2\n", 415, "unsupported_file_type"),
        ("large.csv", b"x" * (MAX_IMPORT_BYTES + 1), 413, "file_too_large"),
        ("broken.xlsx", b"not a workbook", 422, "corrupt_xlsx"),
    ],
)
def test_api_rejects_wrong_type_oversize_and_corrupt_files_without_500(
    filename: str, data: bytes, status: int, code: str,
) -> None:
    response = TestClient(app).post(
        "/api/import-schedule", files={"file": (filename, data, "application/octet-stream")},
    )
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_manual_mapping_is_validated_and_applied_before_project_creation() -> None:
    data = b"Load;Kilowatts\nKitchen;3.5\n"
    mapping = {"Load": "description", "Kilowatts": "power_kw"}
    response = TestClient(app).post(
        "/api/import-schedule",
        files={"file": ("manual.csv", data, "text/csv")},
        data={"mapping": json.dumps(mapping), "confirmed": "true"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assumptions_confirmed"] is True
    assert payload["project_draft"]["circuits"][0]["request"]["load"]["power_w"] == 3500
    assert {item["method"] for item in payload["mapping"]} == {"manual"}

    duplicate = TestClient(app).post(
        "/api/import-schedule",
        files={"file": ("manual.csv", data, "text/csv")},
        data={"mapping": json.dumps({"Load": "description", "Kilowatts": "description"})},
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "duplicate_mapping_field"


class _MappingClient:
    def __init__(self) -> None:
        self.user = ""

    def complete(self, **kwargs: Any) -> str:
        self.user = str(kwargs["user"])
        return '{"mapping":{"Mystery name":"description","P, кВт":"power_kw"}}'


def test_llm_receives_headers_only_and_cannot_change_cell_values() -> None:
    sentinel = "CELL_SECRET_SENTINEL"
    data = f"Mystery name;P, кВт\n{sentinel};7,25\n".encode()
    client = _MappingClient()
    result = import_schedule(
        filename="llm.csv", data=data, pack=load_data_pack(),
        llm_client=client, llm_model="fake",
    )

    assert sentinel not in client.user
    assert result.preview[0]["Mystery name"] == sentinel
    circuit = result.project_draft["circuits"][0]
    assert circuit["request"]["load"]["description"] == sentinel
    assert circuit["request"]["load"]["power_w"] == 7250
    assert {item.method for item in result.mapping if item.field} == {"heuristic", "llm"}


def test_confirmed_imports_get_fresh_project_and_circuit_ids() -> None:
    data = _xlsx_bytes()
    first = import_schedule(filename="fresh.xlsx", data=data, pack=load_data_pack(), confirmed=True)
    second = import_schedule(filename="fresh.xlsx", data=data, pack=load_data_pack(), confirmed=True)

    assert first.project_draft["id"] != second.project_draft["id"]
    assert first.project_draft["circuits"][0]["id"] != second.project_draft["circuits"][0]["id"]
    declaration = first.project_draft["circuits"][0]["meta"]["import_declaration"]
    assert declaration["mapping_confirmed"] is True
    assert first.project_draft["import_info"]["assumptions_confirmed"] is True


def test_r11_trusted_match_error_warning_and_missing_input(verified_pack: DataPack) -> None:
    matched = import_schedule(filename="matched.xlsx", data=_xlsx_bytes(), pack=verified_pack)
    match_report = build_import_diff(matched.project_draft, verified_pack)
    assert match_report.rows[0].status == "match"
    assert {check.status for check in match_report.rows[0].checks} == {"match"}

    bad_row = GOOD_ROW.copy()
    bad_row[8] = 99
    bad_row[11] = 0.5
    mismatched = import_schedule(
        filename="bad.xlsx", data=_xlsx_bytes(rows=[bad_row]), pack=verified_pack,
    )
    bad_report = build_import_diff(mismatched.project_draft, verified_pack)
    assert bad_report.rows[0].status == "violation"
    statuses = {check.field: (check.status, check.severity) for check in bad_report.rows[0].checks}
    assert statuses["declared_section_mm2"] == ("violation", "error")
    assert statuses["declared_in_a"] == ("violation", "warning")

    missing_row = GOOD_ROW.copy()
    missing_row[8] = ""
    missing_row[11] = ""
    missing = import_schedule(
        filename="missing.xlsx", data=_xlsx_bytes(rows=[missing_row]), pack=verified_pack,
    )
    assert {check.reason for check in missing.diff.rows[0].checks} == {"missing_input"}


def test_r11_untrusted_pack_never_presents_mismatch_as_violation() -> None:
    bad_row = GOOD_ROW.copy()
    bad_row[8] = 99
    bad_row[11] = 0.5
    result = import_schedule(
        filename="untrusted.xlsx", data=_xlsx_bytes(rows=[bad_row]), pack=load_data_pack(),
    )

    row = result.diff.rows[0]
    assert row.status == "not_checked"
    assert row.source_trusted is False
    assert {check.reason for check in row.checks} == {"untrusted_source"}
    assert "UNSIGNED_ADVISORY" in result.diff.signoff_notice
    assert result.diff.disclaimer


def test_r11_does_not_present_failed_sizing_candidate_as_requirement(
    verified_pack: DataPack,
) -> None:
    failed_row = GOOD_ROW.copy()
    failed_row[6] = 100000
    result = import_schedule(
        filename="failed-sizing.xlsx",
        data=_xlsx_bytes(rows=[failed_row]),
        pack=verified_pack,
    )

    assert result.diff.rows[0].status == "not_checked"
    assert {check.reason for check in result.diff.rows[0].checks} == {"sizing_failed"}


def test_studio_requires_review_and_confirmation_before_localstorage_write() -> None:
    app_js = (Path(__file__).parents[1] / "web/app.js").read_text(encoding="utf-8")
    confirmed_call = "submitScheduleImport(true)"
    validation_call = "postJSON('/api/project-validate', { project: result.project_draft })"
    storage_call = "projSet(validated.project)"
    assert app_js.index(confirmed_call) < app_js.index(validation_call) < app_js.index(storage_call)
    assert "currentImportMapping()" in app_js
    assert "Я проверил mapping" in (Path(__file__).parents[1] / "web/index.html").read_text()
