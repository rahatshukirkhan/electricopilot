#!/usr/bin/env python3
"""Resolve `doc_id`/`anchor` for pue-rk citations against the ingested ПУЭ text (docs/20 §9).

Values are set BY CODE, from the loaded norm text — never by hand and never by a model
(docs/20 §9). The rule is deliberately strict: a phrase must match EXACTLY ONE stored section
and that section must carry a real z-anchor. Anything ambiguous or unanchored stays `None`, and
an unresolved citation renders exactly as it does today. One verified anchor is worth more than
five guessed ones.

This script never touches a number: it writes only `doc_id`/`anchor` into `_citation`/`citations`
blocks and bumps `meta.version`, then proves the numeric payload is byte-identical before and
after (docs/20 §12.5).

Prerequisite: the ПУЭ must already be in the library — `uv run electricopilot norms ingest
V1500010851 --offline` (or with Neon).

Usage:
    uv run python scripts/resolve_pue_citations.py [--offline] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from electricopilot.calculation_manifest import canonical_json_bytes
from electricopilot.norms.store import NormStore, open_store

PUE_DOC_ID = "V1500010851"
PACK_PATH = Path(__file__).resolve().parents[1] / "src/electricopilot/data/packs/pue-rk.json"
NEW_VERSION = "0.2.0"

#: pack location → the exact wording that identifies the clause in the source text.
#: Only ПУЭ-sourced citations appear here; the IEC ones (IB_load, In_selection,
#: coord_overload, voltage_drop) are not in this document at all and stay unresolved, as does
#: vd_limits_pct — its `_citation.note` already says it needs a separate source.
TARGETS: dict[str, str] = {
    "ampacity._citation": "Таблица 4.",
    "ambient_correction._citation": "Таблица 3.",
    "k_material._citation": "Таблица 48.",
    # п. 40 не имеет собственного заголовка-таблицы; уникальный якорь даёт та самая фраза,
    # из которой extract_pue_rk.py разбирает коэффициенты 0,68/0,63/0,6.
    "grouping_correction._citation": "для 5 и 6",
    "citations.sc_adiabatic": "Таблица 48.",
    "citations.install_method": "Таблица 4.",
}


def _resolve(store: NormStore, phrase: str) -> str | None:
    """Unique anchored section containing `phrase`, or None."""
    matches = [
        section
        for section in store.list_sections(PUE_DOC_ID)
        if phrase in f"{section.heading or ''} {section.body}"
    ]
    if len(matches) != 1:
        return None
    return matches[0].anchor


def _at(pack: dict[str, Any], path: str) -> dict[str, Any] | None:
    node: Any = pack
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def numeric_payload(pack: dict[str, Any]) -> dict[str, Any]:
    """The pack with every citation/meta block stripped — what must not change (§12.5)."""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: strip(value)
                for key, value in node.items()
                if key not in {"_citation", "citations", "meta"}
            }
        if isinstance(node, list):
            return [strip(item) for item in node]
        return node

    return dict(strip(pack))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="читать JSON-снапшоты вместо Neon")
    ap.add_argument("--dry-run", action="store_true", help="только отчёт, файл не писать")
    args = ap.parse_args()

    store = open_store(offline=args.offline)
    if store.get_document(PUE_DOC_ID) is None:
        print(
            f"{PUE_DOC_ID} нет в библиотеке. Сначала: "
            f"uv run electricopilot norms ingest {PUE_DOC_ID}",
            file=sys.stderr,
        )
        return 1

    original = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    pack = json.loads(json.dumps(original))  # deep copy

    resolved, unresolved = 0, []
    for path, phrase in TARGETS.items():
        block = _at(pack, path)
        if block is None:
            unresolved.append(f"{path} (нет такого раздела в паке)")
            continue
        anchor = _resolve(store, phrase)
        if anchor is None:
            unresolved.append(f"{path} (фраза {phrase!r} не даёт единственного совпадения)")
            continue
        block["doc_id"] = PUE_DOC_ID
        block["anchor"] = anchor
        resolved += 1
        print(f"  ✓ {path:34} {phrase!r} → {PUE_DOC_ID}#{anchor}")

    for item in unresolved:
        print(f"  · не сопоставлено: {item}")

    pack["meta"]["version"] = NEW_VERSION
    note = pack["meta"]["source_note"]
    marker = "Версия 0.2.0:"
    if marker not in note:
        pack["meta"]["source_note"] = (
            f"{note} {marker} к цитатам добавлены ссылки на пункты норм "
            "(doc_id/anchor нормативной библиотеки); числовые значения не пересматривались."
        )

    before = canonical_json_bytes(numeric_payload(original))
    after = canonical_json_bytes(numeric_payload(pack))
    if before != after:
        print("ОСТАНОВЛЕНО: числовая часть пака изменилась — это недопустимо.", file=sys.stderr)
        return 2
    print(f"\nчисловая часть пака побайтно не изменилась ({len(before)} байт)")
    print(f"сопоставлено цитат: {resolved}, не сопоставлено: {len(unresolved)}")

    if args.dry_run:
        print("--dry-run: файл не записан")
        return 0
    PACK_PATH.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"записан {PACK_PATH} (meta.version → {NEW_VERSION})")
    print("Дальше ОБЯЗАТЕЛЬНО: uv run python scripts/pack_review.py pue-rk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
