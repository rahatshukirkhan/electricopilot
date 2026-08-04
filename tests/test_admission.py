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


def test_health_reports_admission_mode(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        studio_api, "get_config", lambda: _live_config(llm_admission_mode="disabled"),
    )
    payload = TestClient(studio_api.app).get("/api/health").json()

    assert payload["llm_admission"] == "disabled"


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
