"""Workspace-scoped project storage and opaque read-only shares (docs/15)."""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .project_contract import Project, project_payload, validate_project

PROJECT_STORE_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    workspace TEXT NOT NULL,
    name TEXT NOT NULL,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS projects_workspace_updated_idx
    ON projects (workspace, updated_at DESC);
CREATE TABLE IF NOT EXISTS shares (
    token TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = _utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ProjectRecord:
    project: Project
    updated_at: datetime

    def payload(self) -> dict[str, Any]:
        return project_payload(self.project)


class ProjectConflictError(Exception):
    """An older or ambiguous client snapshot attempted to replace a newer record."""

    def __init__(self, current: ProjectRecord) -> None:
        self.current = current
        super().__init__("project changed after the client snapshot")


class ProjectNotFoundError(Exception):
    """The project does not exist in the caller's workspace."""


class ProjectStore(Protocol):
    def list(self, workspace: str) -> list[ProjectRecord]: ...

    def get(self, workspace: str, project_id: str) -> ProjectRecord | None: ...

    def put(self, workspace: str, project: Project) -> ProjectRecord: ...

    def delete(self, workspace: str, project_id: str) -> bool: ...

    def share(self, workspace: str, project_id: str) -> str: ...

    def get_shared(self, token: str) -> ProjectRecord | None: ...


def _canonical_record(project: Project, updated_at: datetime | None = None) -> ProjectRecord:
    stamp = updated_at or _timestamp(project.updated_at)
    canonical = validate_project(project).model_copy(update={"updated_at": _iso(stamp)})
    return ProjectRecord(project=canonical, updated_at=stamp)


def _same_project(left: ProjectRecord, right: ProjectRecord) -> bool:
    return left.payload() == right.payload()


def _reject_stale(current: ProjectRecord, incoming: ProjectRecord) -> None:
    if incoming.updated_at < current.updated_at:
        raise ProjectConflictError(current)
    if incoming.updated_at == current.updated_at and not _same_project(current, incoming):
        raise ProjectConflictError(current)


class MemoryStore:
    """Deterministic offline implementation used by the shared contract tests."""

    def __init__(self) -> None:
        self._projects: dict[str, tuple[str, ProjectRecord]] = {}
        self._shares: dict[str, str] = {}

    def list(self, workspace: str) -> list[ProjectRecord]:
        records = [record for owner, record in self._projects.values() if owner == workspace]
        return sorted(records, key=lambda item: (item.updated_at, item.project.id), reverse=True)

    def get(self, workspace: str, project_id: str) -> ProjectRecord | None:
        stored = self._projects.get(project_id)
        if stored is None or stored[0] != workspace:
            return None
        return _canonical_record(stored[1].project, stored[1].updated_at)

    def put(self, workspace: str, project: Project) -> ProjectRecord:
        incoming = _canonical_record(project)
        stored = self._projects.get(project.id)
        if stored is not None and stored[0] != workspace:
            raise ProjectNotFoundError(project.id)
        if stored is not None:
            _reject_stale(stored[1], incoming)
            if _same_project(stored[1], incoming):
                return self.get(workspace, project.id)  # type: ignore[return-value]
        self._projects[project.id] = (workspace, incoming)
        return self.get(workspace, project.id)  # type: ignore[return-value]

    def delete(self, workspace: str, project_id: str) -> bool:
        stored = self._projects.get(project_id)
        if stored is None or stored[0] != workspace:
            return False
        del self._projects[project_id]
        self._shares = {token: pid for token, pid in self._shares.items() if pid != project_id}
        return True

    def share(self, workspace: str, project_id: str) -> str:
        if self.get(workspace, project_id) is None:
            raise ProjectNotFoundError(project_id)
        token = secrets.token_urlsafe(32)
        while token in self._shares:
            token = secrets.token_urlsafe(32)
        self._shares[token] = project_id
        return token

    def get_shared(self, token: str) -> ProjectRecord | None:
        project_id = self._shares.get(token)
        if project_id is None:
            return None
        owner, record = self._projects[project_id]
        return self.get(owner, record.project.id)


class PostgresStore:
    """Neon/Postgres implementation; every operation uses one short connection."""

    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise ValueError("Postgres DSN must not be empty")
        self._dsn = dsn

    def init_schema(self) -> None:
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cursor:
                cursor.execute(PROJECT_STORE_DDL)
            conn.commit()

    @staticmethod
    def _record(data: Any, updated_at: datetime) -> ProjectRecord:
        payload = json.loads(data) if isinstance(data, str) else data
        return _canonical_record(validate_project(payload), _timestamp(updated_at))

    def list(self, workspace: str) -> list[ProjectRecord]:
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT data, updated_at FROM projects WHERE workspace = %s "
                "ORDER BY updated_at DESC, id DESC",
                (workspace,),
            )
            return [self._record(data, updated_at) for data, updated_at in cursor.fetchall()]

    def get(self, workspace: str, project_id: str) -> ProjectRecord | None:
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT data, updated_at FROM projects WHERE id = %s AND workspace = %s",
                (project_id, workspace),
            )
            row = cursor.fetchone()
            return self._record(row[0], row[1]) if row else None

    def put(self, workspace: str, project: Project) -> ProjectRecord:
        import psycopg
        from psycopg.types.json import Jsonb

        incoming = _canonical_record(project)
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT workspace, data, updated_at FROM projects WHERE id = %s FOR UPDATE",
                    (project.id,),
                )
                row = cursor.fetchone()
                if row is not None and row[0] != workspace:
                    raise ProjectNotFoundError(project.id)
                if row is not None:
                    current = self._record(row[1], row[2])
                    _reject_stale(current, incoming)
                    if _same_project(current, incoming):
                        return current
                cursor.execute(
                    "INSERT INTO projects (id, workspace, name, data, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, "
                    "data = EXCLUDED.data, updated_at = EXCLUDED.updated_at",
                    (
                        project.id,
                        workspace,
                        incoming.project.name,
                        Jsonb(incoming.payload()),
                        incoming.updated_at,
                    ),
                )
            conn.commit()
        return incoming

    def delete(self, workspace: str, project_id: str) -> bool:
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM projects WHERE id = %s AND workspace = %s",
                    (project_id, workspace),
                )
                deleted = int(cursor.rowcount) > 0
            conn.commit()
        return deleted

    def share(self, workspace: str, project_id: str) -> str:
        import psycopg

        token = secrets.token_urlsafe(32)
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM projects WHERE id = %s AND workspace = %s",
                    (project_id, workspace),
                )
                if cursor.fetchone() is None:
                    raise ProjectNotFoundError(project_id)
                cursor.execute(
                    "INSERT INTO shares (token, project_id) VALUES (%s, %s)",
                    (token, project_id),
                )
            conn.commit()
        return token

    def get_shared(self, token: str) -> ProjectRecord | None:
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT p.data, p.updated_at FROM shares s "
                "JOIN projects p ON p.id = s.project_id WHERE s.token = %s",
                (token,),
            )
            row = cursor.fetchone()
            return self._record(row[0], row[1]) if row else None
