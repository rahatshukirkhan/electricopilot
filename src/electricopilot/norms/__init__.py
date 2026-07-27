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

__all__ = [
    "ADILET_BASE",
    "DocLinks",
    "HistoryEntry",
    "LinkEdge",
    "NormDocument",
    "NormFetchError",
    "NormSection",
    "ParsedDoc",
    "ParsedNorm",
    "Section",
    "doc_url",
    "fetch_cached",
    "fetch_document",
    "fetch_url",
    "id_from_href",
    "looks_contentless",
    "parse_body_links",
    "parse_doc",
    "parse_document",
    "parse_history",
    "parse_info",
    "parse_links",
    "passes_integrity_gate",
    "source_href",
]
