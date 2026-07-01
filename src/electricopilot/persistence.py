"""Persist audit sessions: Neon/Postgres if DATABASE_URL, else JSONL ./runs/ (docs/02 §2.2)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .config import get_config
from .models import SizingResult

_DDL = """
CREATE TABLE IF NOT EXISTS sizing_sessions (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    overall_status TEXT,
    section_mm2 DOUBLE PRECISION,
    in_a DOUBLE PRECISION,
    governing TEXT,
    result JSONB
);
"""


def _session_id(result: SizingResult, ts: str) -> str:
    h = hashlib.sha1(result.model_dump_json().encode("utf-8")).hexdigest()[:10]
    return f"{ts.replace(':', '').replace('-', '')[:15]}-{h}"


def persist(result: SizingResult, *, prefer_jsonl: bool = False) -> str:
    """Store the result; return a location id (neon:<id>) or JSONL file path.
    prefer_jsonl forces the offline JSONL sink even when DATABASE_URL is set."""
    cfg = get_config()
    ts = datetime.now(timezone.utc).isoformat()
    sid = _session_id(result, ts)
    if cfg.db_available and not prefer_jsonl:
        try:
            return _persist_neon(result, sid, cfg.database_url)
        except Exception as exc:  # noqa: BLE001 - degrade to JSONL, never lose the audit
            return _persist_jsonl(result, sid, ts, note=f"neon failed: {exc}")
    return _persist_jsonl(result, sid, ts)


def _persist_neon(result: SizingResult, sid: str, dsn: str) -> str:
    import psycopg  # optional dependency (extra: db)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            cur.execute(
                "INSERT INTO sizing_sessions (id, overall_status, section_mm2, in_a, governing, result)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (sid, result.overall_status, result.selected_cable.cross_section_mm2,
                 result.selected_protection.In_a, result.selected_cable.governing_constraint,
                 result.model_dump_json()),
            )
        conn.commit()
    return f"neon:{sid}"


def _persist_jsonl(result: SizingResult, sid: str, ts: str, note: str = "") -> str:
    runs = Path("runs")
    runs.mkdir(exist_ok=True)
    path = runs / "sessions.jsonl"
    line = (
        '{"id": "%s", "created_at": "%s", "overall_status": "%s", "note": "%s", "result": %s}'
        % (sid, ts, result.overall_status, note, result.model_dump_json())
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return str(path)
