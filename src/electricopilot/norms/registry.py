"""Corpus registry: which documents the norm library is allowed to hold (docs/20 §4.7)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from electricopilot.exceptions import ElectriCopilotError

REGISTRY_PATH = Path(__file__).with_name("registry.json")


class NormRegistryError(ElectriCopilotError):
    """A document was requested that the committed corpus registry does not list."""


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    title: str
    kind: str
    discipline: str
    why: str


@lru_cache(maxsize=1)
def load_registry() -> tuple[RegistryEntry, ...]:
    """Corpus entries in file order (the order `norms ingest` walks without arguments)."""
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return tuple(
        RegistryEntry(
            id=item["id"],
            title=item["title"],
            kind=item["kind"],
            discipline=item["discipline"],
            why=item["why"],
        )
        for item in payload["documents"]
    )


def registry_ids() -> tuple[str, ...]:
    return tuple(entry.id for entry in load_registry())


def require_entry(doc_id: str) -> RegistryEntry:
    """Registry gate.

    Refusing an unlisted ID is a POLICY control, not tidiness: docs/20 §1.4 forbids using
    adilet's search endpoint, so the only sanctioned way a new ID enters the corpus is a
    reviewed edit of `registry.json` (typically after finding the ID in the link graph of a
    document we already hold). Do not relax this into "fetch whatever the caller passed".
    """
    for entry in load_registry():
        if entry.id == doc_id:
            return entry
    known = ", ".join(registry_ids())
    raise NormRegistryError(
        f"{doc_id}: документа нет в реестре корпуса ({REGISTRY_PATH.name}). "
        f"В реестре: {known}. Добавление документа — правка реестра в PR (docs/20 §1.4, §4.7)."
    )
