"""Norm-library storage: Neon/Postgres, plus an offline JSON backend (docs/20 §3, §4.6).

Style follows `electricopilot.store` (DDL as a string, `psycopg`, idempotent upsert, no ORM).
The offline backend is not a test double: running without `DATABASE_URL` is a supported mode
across the whole project, so both backends implement the same `NormStore` protocol and are
covered by the same contract tests.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from electricopilot.exceptions import ElectriCopilotError
from electricopilot.norms.parse import LinkEdge, NormDocument, NormSection, NormStatus

# docs/20 §3, verbatim. Verified against the live Neon instance 2026-07-28: the generated
# `tsvector` column is accepted as written (the one-argument-config form of to_tsvector is
# treated as immutable when the config is a constant), so PR 3's search can rely on it.
NORM_STORE_DDL = """
CREATE TABLE IF NOT EXISTS norm_documents (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL,
    discipline    TEXT NOT NULL DEFAULT 'electrical',
    status        TEXT NOT NULL,
    status_note   TEXT,
    source_url    TEXT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL,
    content_sha256 TEXT NOT NULL,
    meta          JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS norm_sections (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES norm_documents(id) ON DELETE CASCADE,
    anchor      TEXT NOT NULL,
    ordinal     INTEGER NOT NULL,
    breadcrumb  TEXT NOT NULL,
    heading     TEXT,
    body        TEXT NOT NULL,
    has_table   BOOLEAN NOT NULL DEFAULT FALSE,
    tsv tsvector GENERATED ALWAYS AS (
                    to_tsvector('russian', coalesce(breadcrumb,'') || ' ' ||
                                           coalesce(heading,'')    || ' ' || body)
                ) STORED
);
CREATE INDEX IF NOT EXISTS norm_sections_tsv_idx  ON norm_sections USING GIN (tsv);
CREATE INDEX IF NOT EXISTS norm_sections_doc_idx  ON norm_sections (doc_id, ordinal);

CREATE TABLE IF NOT EXISTS norm_links (
    from_doc TEXT NOT NULL REFERENCES norm_documents(id) ON DELETE CASCADE,
    to_doc   TEXT NOT NULL,
    anchor   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (from_doc, to_doc, anchor)
);
"""

# docs/20 §3 declares `anchor TEXT` (nullable) yet puts it in the composite primary key. In
# Postgres that combination is unusable: a NULL in any PK column is rejected (verified against
# Neon — NotNullViolation). A link that names no clause is the common case and carries real
# navigation value, so it is stored with an empty-string anchor rather than dropped, and read
# back as None. Dropping such edges would gut the link graph that §1.2 resolves editions with.
_NO_ANCHOR = ""


class NormStoreError(ElectriCopilotError):
    """A norm document could not be written to or read from the store."""


@dataclass(frozen=True)
class DocumentRecord:
    document: NormDocument
    kind: str
    discipline: str
    fetched_at: datetime
    section_count: int


@dataclass(frozen=True)
class UpsertOutcome:
    doc_id: str
    changed: bool  # content_sha256 differed from what was stored (sections were rewritten)
    sections_written: int


class NormStore(Protocol):
    def init_schema(self) -> None: ...

    def upsert_document(
        self,
        document: NormDocument,
        sections: list[NormSection],
        links: list[LinkEdge],
        *,
        kind: str,
        discipline: str,
        fetched_at: datetime | None = None,
    ) -> UpsertOutcome: ...

    def list_documents(self) -> list[DocumentRecord]: ...

    def get_document(self, doc_id: str) -> DocumentRecord | None: ...

    def list_sections(self, doc_id: str) -> list[NormSection]: ...

    def get_section(self, doc_id: str, anchor: str) -> NormSection | None: ...

    def links_from(self, doc_id: str) -> list[LinkEdge]: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def storable_sections(sections: list[NormSection]) -> list[NormSection]:
    """Sections that can be persisted: the ones the source gave a real z-anchor.

    Anchorless fragments are DROPPED, by customer decision 2026-07-28 (measured cost across the
    corpus: one paragraph — the repeal footnote, whose text is already kept verbatim in
    `norm_documents.status_note`). Inventing a synthetic anchor is forbidden (§8.1: it would
    produce an "открыть пункт" link that 404-scrolls on adilet). Ingest counts what it drops
    into `meta.skipped_anchorless` so the loss is visible rather than silent.
    """
    return [s for s in sections if s.anchor]


def _document_payload(record: DocumentRecord) -> dict[str, Any]:
    doc = record.document
    return {
        "id": doc.id,
        "title": doc.title,
        "kind": record.kind,
        "discipline": record.discipline,
        "status": doc.status,
        "status_note": doc.status_note,
        "source_url": doc.source_url,
        "fetched_at": record.fetched_at.astimezone(timezone.utc).isoformat(),
        "content_sha256": doc.content_sha256,
        "meta": doc.meta,
        "subtitle": doc.subtitle,
        "freshness": doc.freshness,
        "status_conflict": doc.status_conflict,
    }


def _document_from_payload(payload: dict[str, Any], section_count: int) -> DocumentRecord:
    status: NormStatus = payload["status"]
    document = NormDocument(
        id=payload["id"],
        title=payload["title"],
        subtitle=payload.get("subtitle", ""),
        freshness=payload.get("freshness"),
        status=status,
        status_note=payload.get("status_note"),
        status_conflict=payload.get("status_conflict"),
        source_url=payload["source_url"],
        content_sha256=payload["content_sha256"],
        meta=payload.get("meta", {}),
    )
    return DocumentRecord(
        document=document,
        kind=payload["kind"],
        discipline=payload["discipline"],
        fetched_at=datetime.fromisoformat(payload["fetched_at"]),
        section_count=section_count,
    )


def _section_payload(section: NormSection) -> dict[str, Any]:
    return {
        "id": section.id,
        "anchor": section.anchor,
        "ordinal": section.ordinal,
        "breadcrumb": section.breadcrumb,
        "heading": section.heading,
        "body": section.body,
        "has_table": section.has_table,
    }


def _section_from_payload(doc_id: str, payload: dict[str, Any]) -> NormSection:
    return NormSection(
        doc_id=doc_id,
        anchor=payload["anchor"],
        ordinal=payload["ordinal"],
        breadcrumb=payload["breadcrumb"],
        heading=payload["heading"],
        body=payload["body"],
        has_table=payload["has_table"],
    )


class JsonNormStore:
    """Offline backend: one JSON snapshot per document under `./runs/norms/`.

    Writes go through a temp file plus `os.replace`, which is atomic on POSIX — that is how
    this backend honours §4.6's "секции перезаписываются целиком в одной транзакции, никаких
    частичных состояний". A crash mid-write leaves the previous snapshot intact.
    """

    def __init__(self, root: Path | str = Path("runs/norms")) -> None:
        self._root = Path(root)

    def init_schema(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, doc_id: str) -> Path:
        return self._root / f"{doc_id}.json"

    def _read(self, doc_id: str) -> dict[str, Any] | None:
        path = self._path(doc_id)
        if not path.is_file():
            return None
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def upsert_document(
        self,
        document: NormDocument,
        sections: list[NormSection],
        links: list[LinkEdge],
        *,
        kind: str,
        discipline: str,
        fetched_at: datetime | None = None,
    ) -> UpsertOutcome:
        self.init_schema()
        kept = storable_sections(sections)
        previous = self._read(document.id)
        changed = previous is None or previous["document"]["content_sha256"] != document.content_sha256
        record = DocumentRecord(
            document=document,
            kind=kind,
            discipline=discipline,
            fetched_at=fetched_at or _utc_now(),
            section_count=len(kept),
        )
        snapshot = {
            "document": _document_payload(record),
            "sections": [_section_payload(s) for s in kept],
            "links": [
                {"to_doc": link.id, "anchor": link.anchor or _NO_ANCHOR} for link in links
            ],
        }
        path = self._path(document.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)  # atomic swap — never a half-written snapshot
        return UpsertOutcome(doc_id=document.id, changed=changed, sections_written=len(kept))

    def list_documents(self) -> list[DocumentRecord]:
        if not self._root.is_dir():
            return []
        records = []
        for path in sorted(self._root.glob("*.json")):
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            records.append(
                _document_from_payload(snapshot["document"], len(snapshot["sections"]))
            )
        return records

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        snapshot = self._read(doc_id)
        if snapshot is None:
            return None
        return _document_from_payload(snapshot["document"], len(snapshot["sections"]))

    def list_sections(self, doc_id: str) -> list[NormSection]:
        snapshot = self._read(doc_id)
        if snapshot is None:
            return []
        rows = sorted(snapshot["sections"], key=lambda item: int(item["ordinal"]))
        return [_section_from_payload(doc_id, row) for row in rows]

    def get_section(self, doc_id: str, anchor: str) -> NormSection | None:
        for section in self.list_sections(doc_id):
            if section.anchor == anchor:
                return section
        return None

    def links_from(self, doc_id: str) -> list[LinkEdge]:
        snapshot = self._read(doc_id)
        if snapshot is None:
            return []
        return [
            LinkEdge(id=row["to_doc"], anchor=row["anchor"] or None)
            for row in snapshot["links"]
        ]


class PostgresNormStore:
    """Neon/Postgres backend; every operation uses one short connection."""

    def __init__(self, dsn: str) -> None:
        if not dsn.strip():
            raise ValueError("Postgres DSN must not be empty")
        self._dsn = dsn

    def init_schema(self) -> None:
        import psycopg

        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cursor:
                cursor.execute(NORM_STORE_DDL)
            conn.commit()

    def upsert_document(
        self,
        document: NormDocument,
        sections: list[NormSection],
        links: list[LinkEdge],
        *,
        kind: str,
        discipline: str,
        fetched_at: datetime | None = None,
    ) -> UpsertOutcome:
        import psycopg
        from psycopg.types.json import Jsonb

        kept = storable_sections(sections)
        stamp = fetched_at or _utc_now()
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT content_sha256 FROM norm_documents WHERE id = %s FOR UPDATE",
                    (document.id,),
                )
                row = cursor.fetchone()
                changed = row is None or row[0] != document.content_sha256
                cursor.execute(
                    "INSERT INTO norm_documents (id, title, kind, discipline, status, "
                    "status_note, source_url, fetched_at, content_sha256, meta) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, "
                    "kind = EXCLUDED.kind, discipline = EXCLUDED.discipline, "
                    "status = EXCLUDED.status, status_note = EXCLUDED.status_note, "
                    "source_url = EXCLUDED.source_url, fetched_at = EXCLUDED.fetched_at, "
                    "content_sha256 = EXCLUDED.content_sha256, meta = EXCLUDED.meta",
                    (
                        document.id, document.title, kind, discipline, document.status,
                        document.status_note, document.source_url, stamp,
                        document.content_sha256, Jsonb(document.meta),
                    ),
                )
                # Sections and links are replaced WHOLESALE inside this one transaction
                # (docs/20 §4.6) — a partial rewrite would leave orphaned clauses that search
                # would happily return as current text.
                cursor.execute("DELETE FROM norm_sections WHERE doc_id = %s", (document.id,))
                cursor.executemany(
                    "INSERT INTO norm_sections "
                    "(id, doc_id, anchor, ordinal, breadcrumb, heading, body, has_table) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (s.id, s.doc_id, s.anchor, s.ordinal, s.breadcrumb,
                         s.heading, s.body, s.has_table)
                        for s in kept
                    ],
                )
                cursor.execute("DELETE FROM norm_links WHERE from_doc = %s", (document.id,))
                cursor.executemany(
                    "INSERT INTO norm_links (from_doc, to_doc, anchor) VALUES (%s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    [(document.id, link.id, link.anchor or _NO_ANCHOR) for link in links],
                )
            conn.commit()
        return UpsertOutcome(doc_id=document.id, changed=changed, sections_written=len(kept))

    @staticmethod
    def _record(row: Any) -> DocumentRecord:
        meta = row[9] if isinstance(row[9], dict) else json.loads(row[9] or "{}")
        payload = {
            "id": row[0], "title": row[1], "kind": row[2], "discipline": row[3],
            "status": row[4], "status_note": row[5], "source_url": row[6],
            "fetched_at": row[7].astimezone(timezone.utc).isoformat(),
            "content_sha256": row[8], "meta": meta,
            "subtitle": meta.get("registration", ""), "freshness": meta.get("freshness"),
            "status_conflict": None,
        }
        return _document_from_payload(payload, int(row[10]))

    _SELECT = (
        "SELECT d.id, d.title, d.kind, d.discipline, d.status, d.status_note, d.source_url, "
        "d.fetched_at, d.content_sha256, d.meta, "
        "(SELECT count(*) FROM norm_sections s WHERE s.doc_id = d.id) "
        "FROM norm_documents d"
    )

    def list_documents(self) -> list[DocumentRecord]:
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cursor:
            cursor.execute(f"{self._SELECT} ORDER BY d.id")
            return [self._record(row) for row in cursor.fetchall()]

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cursor:
            cursor.execute(f"{self._SELECT} WHERE d.id = %s", (doc_id,))
            row = cursor.fetchone()
            return self._record(row) if row else None

    def list_sections(self, doc_id: str) -> list[NormSection]:
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT anchor, ordinal, breadcrumb, heading, body, has_table "
                "FROM norm_sections WHERE doc_id = %s ORDER BY ordinal",
                (doc_id,),
            )
            return [
                NormSection(doc_id=doc_id, anchor=row[0], ordinal=row[1], breadcrumb=row[2],
                            heading=row[3], body=row[4], has_table=row[5])
                for row in cursor.fetchall()
            ]

    def get_section(self, doc_id: str, anchor: str) -> NormSection | None:
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT anchor, ordinal, breadcrumb, heading, body, has_table "
                "FROM norm_sections WHERE doc_id = %s AND anchor = %s",
                (doc_id, anchor),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return NormSection(doc_id=doc_id, anchor=row[0], ordinal=row[1], breadcrumb=row[2],
                               heading=row[3], body=row[4], has_table=row[5])

    def links_from(self, doc_id: str) -> list[LinkEdge]:
        import psycopg

        with psycopg.connect(self._dsn) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT to_doc, anchor FROM norm_links WHERE from_doc = %s "
                "ORDER BY to_doc, anchor",
                (doc_id,),
            )
            return [LinkEdge(id=row[0], anchor=row[1] or None) for row in cursor.fetchall()]


def open_store(*, offline: bool = False, root: Path | str = Path("runs/norms")) -> NormStore:
    """Postgres when `DATABASE_URL` is set (and `offline` is not forced), else JSON snapshots."""
    from electricopilot.config import get_config

    cfg = get_config()
    if cfg.db_available and not offline:
        return PostgresNormStore(cfg.database_url)
    return JsonNormStore(root)
