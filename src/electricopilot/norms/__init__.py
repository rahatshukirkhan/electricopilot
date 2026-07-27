"""Norm library: fetch + parse of official texts from adilet.zan.kz (docs/20).

Scope discipline (docs/20 §0): this package is a READER of norm texts for humans. Numbers
that reach the deterministic engine still come only from data packs extracted by
`scripts/extract_pue_rk.py`; nothing here feeds the calculation core.
"""
from __future__ import annotations

from electricopilot.norms.fetch import (
    ADILET_BASE,
    NormFetchError,
    doc_url,
    fetch_cached,
    fetch_document,
    fetch_url,
    passes_integrity_gate,
)
from electricopilot.norms.ingest import IngestReport, NormIngestError, ingest_corpus, ingest_document
from electricopilot.norms.parse import (
    DocLinks,
    HistoryEntry,
    LinkEdge,
    NormDocument,
    NormSection,
    ParsedDoc,
    ParsedNorm,
    Section,
    id_from_href,
    looks_contentless,
    parse_body_links,
    parse_doc,
    parse_document,
    parse_history,
    parse_info,
    parse_links,
    source_href,
)
from electricopilot.norms.registry import (
    NormRegistryError,
    RegistryEntry,
    load_registry,
    registry_ids,
    require_entry,
)
from electricopilot.norms.store import (
    NORM_STORE_DDL,
    DocumentRecord,
    JsonNormStore,
    NormStore,
    NormStoreError,
    PostgresNormStore,
    UpsertOutcome,
    open_store,
    storable_sections,
)

__all__ = [
    "ADILET_BASE",
    "DocLinks",
    "DocumentRecord",
    "HistoryEntry",
    "IngestReport",
    "JsonNormStore",
    "LinkEdge",
    "NORM_STORE_DDL",
    "NormDocument",
    "NormFetchError",
    "NormIngestError",
    "NormRegistryError",
    "NormSection",
    "NormStore",
    "NormStoreError",
    "ParsedDoc",
    "ParsedNorm",
    "PostgresNormStore",
    "RegistryEntry",
    "Section",
    "UpsertOutcome",
    "doc_url",
    "fetch_cached",
    "fetch_document",
    "fetch_url",
    "id_from_href",
    "ingest_corpus",
    "ingest_document",
    "load_registry",
    "looks_contentless",
    "open_store",
    "parse_body_links",
    "parse_doc",
    "parse_document",
    "parse_history",
    "parse_info",
    "parse_links",
    "passes_integrity_gate",
    "registry_ids",
    "require_entry",
    "source_href",
    "storable_sections",
]
