"""Norm-library search orchestration: lexical order, optionally reranked (docs/20 §6).

v1 is lexical, without vectors, and that is a deliberate scope call: the corpus is four
documents (legality had 2 727), and the hybrid gain measured there was a property of that
scale. Dragging in embeddings, pgvector and HNSW for four documents is premature complexity.

The optional LLM rerank sits behind `NORMS_LLM_RERANK=1`. The model here does NOT write text
and does NOT touch numbers — it only sorts, and it is trusted to do even that only under a
strict contract: it must return exactly the identifiers it was given. Anything else — an
unknown id, a dropped id, a duplicate, prose around the JSON — discards the whole response and
leaves the deterministic lexical order untouched.
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from electricopilot.exceptions import LlmError
from electricopilot.norms.store import NormStore, SearchHit

#: docs/20 §6 — only the top-30 lexical hits are ever shown to the reranker.
RERANK_POOL = 30

_SYSTEM = (
    "Ты сортируешь фрагменты нормативных документов по релевантности запросу инженера-"
    "электрика. Ты НЕ пишешь текст, НЕ пересказываешь нормы и НЕ называешь числа. "
    "Ответ — только JSON-массив идентификаторов из входного списка, тот же набор, "
    "переупорядоченный. Ничего кроме массива."
)
_USER = (
    "Запрос: {query}\n\n"
    "Фрагменты (идентификатор — заголовок — фрагмент текста):\n{items}\n\n"
    "Верни JSON-массив всех {count} идентификаторов в порядке убывания релевантности."
)
_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


class Completer(Protocol):
    """Just the slice of `OpenRouterClient` the rerank needs (and `FakeClient` satisfies)."""

    def complete(self, *, model: str, system: str, user: str) -> str: ...


def search_norms(
    store: NormStore,
    query: str,
    *,
    doc_ids: list[str] | None = None,
    limit: int = 20,
    include_repealed: bool = False,
    client: Completer | None = None,
    model: str = "",
) -> list[SearchHit]:
    """Ranked sections for `query`. Passing `client` enables the §6 rerank."""
    pool = max(limit, RERANK_POOL) if client is not None else limit
    hits = store.search(query, doc_ids=doc_ids, limit=pool, include_repealed=include_repealed)
    if client is not None:
        hits = rerank_hits(hits, query=query, client=client, model=model)
    return hits[:limit]


def rerank_hits(
    hits: list[SearchHit], *, query: str, client: Completer, model: str
) -> list[SearchHit]:
    """Reorder `hits` with the fast model, or return them untouched if anything is off."""
    if len(hits) < 2:
        return hits
    items = "\n".join(
        f"{hit.section_id} — {hit.heading or hit.breadcrumb or '—'} — {hit.snippet[:200]}"
        for hit in hits
    )
    try:
        raw = client.complete(
            model=model,
            system=_SYSTEM,
            user=_USER.format(query=query, items=items, count=len(hits)),
        )
    except LlmError:
        return hits  # a failed rerank is never a failed search

    order = _parse_order(raw, [hit.section_id for hit in hits])
    if order is None:
        return hits
    by_id = {hit.section_id: hit for hit in hits}
    return [by_id[section_id] for section_id in order]


def _parse_order(raw: str, known: list[str]) -> list[str] | None:
    """Accept only an exact permutation of `known`; anything else is rejected wholesale."""
    text = _FENCE_RE.sub("", raw or "").strip()
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        return None
    if len(parsed) != len(known) or set(parsed) != set(known):
        return None  # unknown id, dropped id or duplicate — discard the whole response
    return list(parsed)
