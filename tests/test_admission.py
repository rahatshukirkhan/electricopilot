"""Offline proof that public-resource rejections happen before live LLM calls."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from electricopilot import studio_api
from electricopilot.admission import admission
from electricopilot.config import Config


def _live_config(**overrides: Any) -> Config:
    values: dict[str, Any] = {
        "openrouter_api_key": "test-key",
        "openrouter_base_url": "https://example.invalid/api/v1",
        "model_strong": "fake/strong",
        "model_fast": "fake/fast",
        "app_title": "test",
        "http_referer": "https://example.invalid",
        "database_url": "",
        "strict_provenance": True,
        "llm_timeout": 60,
        "max_request_bytes": 1024,
        "max_project_circuits": 128,
        "max_concurrent_heavy_operations": 2,
        "llm_admission_mode": "local",
        "llm_requests_per_window": 1,
        "llm_global_requests_per_window": 2,
        "llm_window_seconds": 60,
    }
    values.update(overrides)
    return Config(**values)


def test_oversized_body_is_rejected_before_intake_or_llm(monkeypatch: Any) -> None:
    called = False

    def should_not_run(*_: Any, **__: Any) -> object:
        nonlocal called
        called = True
        raise AssertionError("downstream LLM parser must not run")

    monkeypatch.setattr(studio_api, "get_config", lambda: _live_config())
    monkeypatch.setattr(studio_api, "intake_parse", should_not_run)
    response = TestClient(studio_api.app).post(
        "/api/intake", content=b"{}", headers={"content-length": "1025"},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "request_too_large"
    assert not called


def test_quota_prevents_a_repeated_live_llm_call(monkeypatch: Any) -> None:
    calls = 0

    def parsed(*_: Any, **__: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(model_dump=lambda: {"load": "parsed"})

    admission.reset()
    monkeypatch.setattr(studio_api, "get_config", lambda: _live_config())
    monkeypatch.setattr(studio_api, "intake_parse", parsed)
    client = TestClient(studio_api.app)
    first = client.post("/api/intake", json={"text": "розетка"})
    second = client.post("/api/intake", json={"text": "розетка"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["error"] == "llm_client_quota"
    assert second.headers["retry-after"] == "60"
    assert calls == 1


def test_disabled_admission_never_constructs_live_llm_client(monkeypatch: Any) -> None:
    admission.reset()
    monkeypatch.setattr(
        studio_api, "get_config", lambda: _live_config(llm_admission_mode="disabled"),
    )
    monkeypatch.setattr(
        studio_api, "_client", lambda: (_ for _ in ()).throw(AssertionError("must not construct client")),
    )
    response = TestClient(studio_api.app).post("/api/intake", json={"text": "розетка"})

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "llm_admission_not_configured"


def _minimal_project() -> dict[str, Any]:
    return {
        "id": "project-admission",
        "name": "Admission fixture",
        "board_ref": "AD-1",
        "supply": {"voltage_v": 400, "phases": 3, "ways_total": 12},
        "circuits": [{
            "id": "id-C1",
            "ref": "C1",
            "request": {
                "load": {
                    "description": "C1", "power_w": 2000, "voltage_v": 230,
                    "phases": 1, "power_factor": 1.0, "purpose": "power",
                },
                "installation": {
                    "method": "C", "material": "Cu", "insulation": "PVC",
                    "ambient_temp_c": 30, "grouping_circuits": 1, "length_m": 20,
                },
                "protection": {
                    "device_class": "MCB", "prospective_fault_current_a": 1500,
                    "disconnection_time_s": 0.1, "max_voltage_drop_pct": None,
                    "trip_curve_type": "C",
                },
            },
            "meta": {"phase": "L1", "rcd": {"present": False}},
            "result": None,
        }],
    }


def test_normcheck_degrades_to_no_narrative_when_admission_disabled(monkeypatch: Any) -> None:
    """docs/19 fail-closed protects the LIVE LLM narrative only: with a key present but
    llm_admission_mode="disabled" the deterministic report must still come back as 200,
    just without a narrative — not a 503 for the whole endpoint (see also normcheck's
    test_api_available_without_llm_key, the no-key twin of this case)."""
    admission.reset()
    monkeypatch.setattr(
        studio_api, "get_config", lambda: _live_config(llm_admission_mode="disabled"),
    )
    monkeypatch.setattr(
        studio_api, "_client", lambda: (_ for _ in ()).throw(AssertionError("must not construct client")),
    )
    response = TestClient(studio_api.app).post(
        "/api/normcheck", json={"project": _minimal_project()},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["narrative"] is None
    assert payload["findings"]
    assert payload["summary"]["total"] == len(payload["findings"])


def test_normcheck_still_narrates_when_admission_is_local(monkeypatch: Any) -> None:
    """Companion to the disabled-mode degradation test above: proves the refactored
    `_optional_llm_client` plumbing still wires a real narrative through in the ordinary
    working configuration (mode="local", key present) — a swallowed client or an inverted
    `is not None` check would make this fail while the disabled-mode test would still pass."""
    from electricopilot.models import LlmNarrative

    admission.reset()
    monkeypatch.setattr(studio_api, "get_config", lambda: _live_config())
    monkeypatch.setattr(
        studio_api, "narrate_findings",
        lambda *_a, **_k: LlmNarrative(
            text="SENTINEL_NARRATIVE", model="fake", provenance_ok=True, unverified_numbers=[],
        ),
    )
    response = TestClient(studio_api.app).post(
        "/api/normcheck", json={"project": _minimal_project()},
    )

    assert response.status_code == 200
    assert response.json()["narrative"]["text"] == "SENTINEL_NARRATIVE"


def test_import_schedule_falls_back_to_heuristic_mapping_when_admission_disabled(
    monkeypatch: Any,
) -> None:
    """First-pass import (no manual mapping yet) with a key present but the AI admission
    fail-closed: must take the same heuristic-only path as no key configured, not 503
    before any deterministic parsing result is returned."""
    admission.reset()
    monkeypatch.setattr(
        studio_api, "get_config", lambda: _live_config(llm_admission_mode="disabled"),
    )
    monkeypatch.setattr(
        studio_api, "_client", lambda: (_ for _ in ()).throw(AssertionError("must not construct client")),
    )
    data = b"Load;Kilowatts\nKitchen;3.5\n"
    response = TestClient(studio_api.app).post(
        "/api/import-schedule", files={"file": ("schedule.csv", data, "text/csv")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert {entry["source_header"] for entry in payload["mapping"]} == {"Load", "Kilowatts"}
    assert all(entry["method"] != "llm" for entry in payload["mapping"])


def test_health_reports_the_llm_admission_mode(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        studio_api, "get_config", lambda: _live_config(llm_admission_mode="disabled"),
    )
    response = TestClient(studio_api.app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["llm_admission"] == "disabled"


def test_shared_budget_blocks_a_second_client_before_llm(monkeypatch: Any) -> None:
    calls = 0

    def parsed(*_: Any, **__: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(model_dump=lambda: {"load": "parsed"})

    admission.reset()
    monkeypatch.setattr(
        studio_api,
        "get_config",
        lambda: _live_config(llm_requests_per_window=2, llm_global_requests_per_window=1),
    )
    monkeypatch.setattr(studio_api, "intake_parse", parsed)
    client = TestClient(studio_api.app)
    assert client.post("/api/intake", json={"text": "розетка"}, headers={"x-forwarded-for": "a"}).status_code == 200
    blocked = client.post(
        "/api/intake", json={"text": "розетка"}, headers={"x-forwarded-for": "b"},
    )

    assert blocked.status_code == 503
    assert blocked.json()["detail"]["error"] == "llm_global_budget"
    assert calls == 1
