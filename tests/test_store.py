"""ProjectStore, workspace isolation, sync conflicts, and share API (docs/15)."""
from __future__ import annotations

import inspect
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from electricopilot import studio_api
from electricopilot.project_contract import Project
from electricopilot.store import (
    PROJECT_STORE_DDL,
    MemoryStore,
    PostgresStore,
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectStoreNotInitializedError,
)


def _project(*, updated_at: str = "2026-07-19T12:00:00Z", name: str = "Щит") -> Project:
    return Project.model_validate({
        "schema_version": 2,
        "id": "project-store",
        "name": name,
        "board_ref": "DB-S",
        "created_at": "2026-07-19T11:00:00Z",
        "updated_at": updated_at,
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
        "circuits": [],
    })


def _headers(workspace: str = "workspace-a") -> dict[str, str]:
    return {"X-Workspace": workspace}


def _put(client: TestClient, project: Project, workspace: str = "workspace-a") -> Any:
    return client.put(
        f"/api/projects/{project.id}",
        headers=_headers(workspace),
        json={"project": project.model_dump(mode="json")},
    )


def test_memory_store_contract_is_workspace_scoped_and_share_is_read_only() -> None:
    store = MemoryStore()
    project = _project()

    saved = store.put("workspace-a", project)

    assert saved.project == project
    assert store.list("workspace-a")[0].project.id == project.id
    assert store.list("workspace-b") == []
    assert store.get("workspace-b", project.id) is None
    with pytest.raises(ProjectNotFoundError):
        store.put("workspace-b", project.model_copy(update={"updated_at": "2026-07-19T13:00:00Z"}))

    token = store.share("workspace-a", project.id)
    assert len(token) >= 32
    assert project.id not in token
    assert store.get_shared(token).project == project  # type: ignore[union-attr]
    assert store.delete("workspace-b", project.id) is False
    assert store.delete("workspace-a", project.id) is True
    assert store.get_shared(token) is None


def test_memory_store_rejects_stale_and_ambiguous_snapshots() -> None:
    store = MemoryStore()
    current = store.put("workspace-a", _project(updated_at="2026-07-19T13:00:00Z"))

    with pytest.raises(ProjectConflictError) as stale:
        store.put("workspace-a", _project(updated_at="2026-07-19T12:00:00Z"))
    assert stale.value.current == current

    with pytest.raises(ProjectConflictError):
        store.put("workspace-a", _project(updated_at="2026-07-19T13:00:00Z", name="Другой"))

    assert store.put("workspace-a", current.project) == current
    newer = store.put("workspace-a", _project(updated_at="2026-07-19T14:00:00Z", name="Новый"))
    assert newer.project.name == "Новый"


def test_project_api_covers_crud_conflict_workspace_and_shared_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore()
    monkeypatch.setattr(studio_api, "_PROJECT_STORE_OVERRIDE", store)
    client = TestClient(studio_api.app)
    project = _project()

    missing_workspace = client.get("/api/projects")
    assert missing_workspace.status_code == 400
    assert missing_workspace.json()["detail"]["error"] == "workspace_required"

    created = _put(client, project)
    assert created.status_code == 200
    assert created.json()["project"]["schema_version"] == 2
    assert client.get("/api/projects", headers=_headers()).json()["projects"][0]["id"] == project.id
    assert client.get(f"/api/projects/{project.id}", headers=_headers("workspace-b")).status_code == 404

    stale = _put(client, _project(updated_at="2026-07-19T11:59:00Z", name="Устаревший"))
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"] == "project_conflict"
    assert stale.json()["detail"]["project"]["name"] == project.name

    shared = client.post(f"/api/projects/{project.id}/share", headers=_headers())
    assert shared.status_code == 200
    token = shared.json()["token"]
    response = client.get(f"/api/shared/{token}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["project"]["id"] == project.id
    assert payload["report"]["rows"] == []
    assert "UNSIGNED_ADVISORY" in payload["report"]["signoff_notice"]
    assert payload["report"]["calculation_id"].startswith("calc-v1-")
    assert payload["report"]["calculation_manifest"]["calculation_id"] == (
        payload["report"]["calculation_id"]
    )

    deleted = client.delete(f"/api/projects/{project.id}", headers=_headers())
    assert deleted.json() == {"ok": True}
    assert client.get(f"/api/shared/{token}").status_code == 404


def test_project_api_reports_no_db_without_touching_local_projects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(studio_api, "_PROJECT_STORE_OVERRIDE", None)
    monkeypatch.setattr(
        studio_api,
        "get_config",
        lambda: SimpleNamespace(db_available=False),
    )

    response = TestClient(studio_api.app).get("/api/projects", headers=_headers())

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "no_db"


def test_postgres_queries_are_parameterized_and_schema_is_separate_from_audit() -> None:
    source = inspect.getsource(PostgresStore)

    assert "%s" in source
    assert "f\"SELECT" not in source and "f\"INSERT" not in source
    assert "REFERENCES projects(id) ON DELETE CASCADE" in PROJECT_STORE_DDL
    assert "sizing_sessions" not in PROJECT_STORE_DDL


@pytest.mark.skipif(
    not os.environ.get("ELECTRICOPILOT_TEST_DATABASE_URL"),
    reason="set ELECTRICOPILOT_TEST_DATABASE_URL to opt in to a disposable Postgres smoke test",
)
def test_postgres_store_smoke() -> None:
    dsn = os.environ["ELECTRICOPILOT_TEST_DATABASE_URL"]
    store = PostgresStore(dsn)
    store.init_schema()
    project = _project(updated_at=datetime.now(timezone.utc).isoformat())
    workspace = "pytest-project-store"

    store.delete(workspace, project.id)
    assert store.put(workspace, project).project.id == project.id
    assert store.get(workspace, project.id) is not None
    assert store.get_shared(store.share(workspace, project.id)) is not None
    assert store.delete(workspace, project.id)


def test_frontend_contract_is_local_first_debounced_and_read_only() -> None:
    app_js = (Path(__file__).parents[1] / "web/app.js").read_text(encoding="utf-8")
    html = (Path(__file__).parents[1] / "web/index.html").read_text(encoding="utf-8")

    assert "X-Workspace" in app_js
    assert "2000" in app_js
    assert "project_conflict" in app_js
    assert "() => go('/s/' + payload.token)" in app_js
    assert "project updated" not in app_js  # user-facing conflict notice stays Russian
    assert "#/s/" in app_js or "'/s/'" in app_js
    assert 'id="screen-shared"' in html
    assert 'id="btnShare"' in html


def test_health_reports_local_when_the_schema_was_never_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Найдено аудитом 29.07.2026: в проде DSN был задан, а таблиц не существовало —
    health рапортовал `neon`, фронт включал синхронизацию, и каждый PUT падал в 500."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@localhost/absent")
    monkeypatch.setattr(studio_api, "_PROJECT_STORE_OVERRIDE", None)
    monkeypatch.setattr(PostgresStore, "schema_ready", lambda self: False)

    payload = TestClient(studio_api.app).get("/api/health").json()

    assert payload["project_store"] == "local"

    monkeypatch.setattr(PostgresStore, "schema_ready", lambda self: True)
    ready = TestClient(studio_api.app).get("/api/health").json()
    assert ready["project_store"] == "neon"


def test_missing_schema_is_a_typed_503_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Uninitialized:
        def list(self, workspace: str) -> list[Any]:
            raise ProjectStoreNotInitializedError('relation "projects" does not exist')

    monkeypatch.setattr(studio_api, "_PROJECT_STORE_OVERRIDE", _Uninitialized())
    client = TestClient(studio_api.app, raise_server_exceptions=False)

    response = client.get("/api/projects", headers=_headers())

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "project_store_not_initialized"


def test_connections_are_bounded_so_an_unreachable_database_fails_fast() -> None:
    source = inspect.getsource(PostgresStore)

    assert "connect_timeout=self._connect_timeout" in source
    assert "psycopg.connect(self._dsn)" not in source  # каждый вызов идёт через _connect()
