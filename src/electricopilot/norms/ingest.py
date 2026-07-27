"""Ingest pipeline: adilet page → parsed norm → store (docs/20 §4).

Order of gates matters, and every one of them refuses BEFORE anything is written — a document
that fails any check must not land in the library even partially:

  1. registry gate   — the ID must be listed in `registry.json` (§1.4/§4.7 policy control);
  2. fetch + §4.2 integrity gate — an anti-bot shell never overwrites a good copy;
  3. `looks_contentless` — a page with no legal text is a failure, not an empty document;
  4. status conflict — badge and header footnote disagreeing raises here (§4.4), because
     `parse_document` deliberately returns instead of raising so the doc stays inspectable.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from electricopilot.exceptions import ElectriCopilotError
from electricopilot.norms.fetch import DEFAULT_CACHE_DIR, doc_url, fetch_document
from electricopilot.norms.parse import looks_contentless, parse_document
from electricopilot.norms.registry import registry_ids, require_entry
from electricopilot.norms.store import NormStore, storable_sections

Fetcher = Callable[[str], str]


class NormIngestError(ElectriCopilotError):
    """A document could not be ingested; nothing was written for it."""


@dataclass(frozen=True)
class IngestReport:
    doc_id: str
    title: str
    status: str
    sections_written: int
    sections_skipped: int
    links: int
    changed: bool


def _default_fetcher(cache_dir: Path | None, refresh: bool) -> Fetcher:
    def fetch(doc_id: str) -> str:
        return fetch_document(doc_id, cache_dir=cache_dir, refresh=refresh)

    return fetch


def ingest_document(
    doc_id: str,
    *,
    store: NormStore,
    fetcher: Fetcher | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    refresh: bool = False,
    fetched_at: datetime | None = None,
) -> IngestReport:
    """Fetch, parse and upsert one registry document. Raises rather than writing partial data."""
    entry = require_entry(doc_id)
    fetch = fetcher or _default_fetcher(cache_dir, refresh)
    html = fetch(doc_id)

    if looks_contentless(html):
        raise NormIngestError(
            f"{doc_id}: страница не содержит правового текста (заглушка/капча) — не сохранено."
        )

    parsed = parse_document(html, doc_id=doc_id, source_url=doc_url(doc_id))
    document = parsed.document
    if document.status_conflict is not None:
        raise NormIngestError(
            f"{doc_id}: расхождение сигналов статуса — {document.status_conflict}. "
            f"Статус записан бы как '{document.status}'; ingest остановлен, "
            "нужно решение человека (docs/20 §4.4)."
        )

    kept = storable_sections(parsed.sections)
    skipped = len(parsed.sections) - len(kept)
    if not kept:
        raise NormIngestError(f"{doc_id}: не найдено ни одной секции с якорем — не сохранено.")

    # Record dropped anchorless fragments in `meta` so the loss is visible, not silent.
    document = replace(document, meta={**document.meta, "skipped_anchorless": skipped})
    outcome = store.upsert_document(
        document,
        parsed.sections,
        parsed.links,
        kind=entry.kind,
        discipline=entry.discipline,
        fetched_at=fetched_at or datetime.now(timezone.utc),
    )
    return IngestReport(
        doc_id=doc_id,
        title=document.title,
        status=document.status,
        sections_written=outcome.sections_written,
        sections_skipped=skipped,
        links=len(parsed.links),
        changed=outcome.changed,
    )


def ingest_corpus(
    doc_ids: list[str] | None = None,
    *,
    store: NormStore,
    fetcher: Fetcher | None = None,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    refresh: bool = False,
) -> list[IngestReport]:
    """Ingest the given IDs, or the whole committed registry when none are given (§4.7)."""
    targets = doc_ids or list(registry_ids())
    store.init_schema()
    return [
        ingest_document(
            doc_id, store=store, fetcher=fetcher, cache_dir=cache_dir, refresh=refresh
        )
        for doc_id in targets
    ]
