"""Норм-пак из недоверенного входа резолвится только по имени (аудит бэкенда 29.07.2026).

`load_data_pack` намеренно принимает путь к файлу — это удобство CLI (docs/12 §1.1). Через
HTTP тот же аргумент превращал `?pack=` и `project.norm_pack` в чтение произвольного файла
сервера: ответ 400 отличал «нет файла» от «не JSON» и возвращал часть содержимого JSON в
тексте ошибки pydantic, а `?pack=/dev/zero` съедал память воркера. Эти тесты фиксируют, что
ни один публичный вход больше не доходит до файловой системы.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from electricopilot.data.loader import load_data_pack, load_pack_by_name
from electricopilot.exceptions import DataPackError
from electricopilot.studio_api import app

client = TestClient(app)

REQUEST = {
    "load": {"power_w": 3000, "voltage_v": 230, "phases": 1},
    "installation": {"method": "C", "length_m": 20},
}


def _project(norm_pack: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "id": "prj_pack_admission",
        "name": "проверка пака",
        "board_ref": "DB-1",
        "norm_pack": norm_pack,
        "supply": {"voltage_v": 400, "phases": 3, "ways_total": 12, "earthing": "TN-S"},
        "circuits": [],
    }


#: Пути, которые раньше уходили в `Path(...).read_text()`.
FILESYSTEM_INPUTS = [
    "/etc/hosts",
    "/etc/nonexistent-electricopilot.json",
    "../../etc/passwd",
    "runs/norms/V1500010851.json",
    "/dev/zero",
]


@pytest.mark.parametrize("pack", FILESYSTEM_INPUTS)
def test_query_pack_never_reaches_the_filesystem(pack: str) -> None:
    response = client.post("/api/size", params={"pack": pack}, json=REQUEST)
    assert response.status_code == 400
    detail = json.dumps(response.json(), ensure_ascii=False)
    # Ни содержимого файла, ни самого пути (различимость ответов и была оракулом).
    assert pack not in detail
    assert "read_text" not in detail and "No such file" not in detail


@pytest.mark.parametrize(
    "path",
    ["/api/project-report", "/api/project-sld", "/api/project-export", "/api/normcheck"],
)
def test_project_norm_pack_never_reaches_the_filesystem(path: str) -> None:
    response = client.post(path, json={"project": _project("/etc/hosts")})
    assert response.status_code == 400
    assert "/etc/hosts" not in json.dumps(response.json(), ensure_ascii=False)


def test_copilot_answers_typed_error_for_an_unknown_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Раньше здесь был необработанный 500: пак резолвится внутри run_copilot."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")
    monkeypatch.setenv("ELECTRICOPILOT_LLM_ADMISSION_MODE", "local")
    local = TestClient(app, raise_server_exceptions=False)
    response = local.post(
        "/api/copilot",
        json={"project": _project("/etc/hosts"), "message": "привет", "history": []},
    )
    assert response.status_code == 400
    assert "/etc/hosts" not in json.dumps(response.json(), ensure_ascii=False)


def test_bundled_pack_names_still_work() -> None:
    response = client.post("/api/size", params={"pack": "pue-rk"}, json=REQUEST)
    assert response.status_code == 200
    assert response.json()["data_pack"]["name"] == "pue-rk"


def test_default_pack_without_parameter() -> None:
    assert client.post("/api/size", json=REQUEST).status_code == 200


def test_load_pack_by_name_rejects_paths_but_load_data_pack_keeps_the_cli_affordance(
    tmp_path: Path,
) -> None:
    bundled = load_data_pack("iec-stub")
    file_pack = tmp_path / "copy.json"
    file_pack.write_text(bundled.model_dump_json(), encoding="utf-8")

    # CLI/скрипты по-прежнему могут загрузить пак из файла...
    assert load_data_pack(str(file_pack)).meta.name == bundled.meta.name
    # ...а недоверенный вход — нет, даже если файл существует и валиден.
    with pytest.raises(DataPackError):
        load_pack_by_name(str(file_pack))
    with pytest.raises(DataPackError):
        load_pack_by_name("no-such-pack")
    assert load_pack_by_name(None).meta.name == "iec-stub"
    assert load_pack_by_name("pue-rk").meta.name == "pue-rk"
