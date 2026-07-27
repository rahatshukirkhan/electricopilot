"""Norm-library registry, store and ingest tests (docs/20 §3, §4, §12.1).

Offline by construction: the HTML comes from `tests/fixtures/norms/`, the store is the JSON
backend under `tmp_path`, and the fetcher is injected — no network, no database.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from electricopilot.norms.ingest import Fetcher, NormIngestError, ingest_corpus, ingest_document
from electricopilot.norms.parse import LinkEdge, NormDocument, NormSection, parse_document
from electricopilot.norms.registry import (
    NormRegistryError,
    load_registry,
    registry_ids,
    require_entry,
)
from electricopilot.norms.store import (
    NORM_STORE_DDL,
    JsonNormStore,
    storable_sections,
)

FIXTURES = Path(__file__).parent / "fixtures" / "norms"
PUE = "V1500010851"
TECHREG = "V2300032783"
REPEALED = "V1900019361"  # утратил силу — в корпус v1 намеренно НЕ входит (docs/20 §1.3)


def fixture_html(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def fake_fetcher(pages: dict[str, str]) -> Fetcher:
    def fetch(doc_id: str) -> str:
        return pages[doc_id]

    return fetch


@pytest.fixture()
def store(tmp_path: Path) -> JsonNormStore:
    created = JsonNormStore(tmp_path / "norms")
    created.init_schema()
    return created


# --------------------------------------------------------------------- registry (§4.7)


def test_registry_holds_corpus_v1() -> None:
    entries = load_registry()
    assert [entry.id for entry in entries] == [
        "V1500010851", "V1500010949", "V1600014771", "V2300032783",
    ]
    assert {entry.discipline for entry in entries} == {"electrical"}
    assert all(entry.why for entry in entries)  # «зачем документ в корпусе» — обязательное поле


def test_repealed_sn_rk_is_not_in_the_corpus() -> None:
    """docs/20 §1.3: показывать утративший силу документ как норму вреднее, чем не показывать."""
    assert REPEALED not in registry_ids()


def test_registry_gate_refuses_an_unlisted_document() -> None:
    with pytest.raises(NormRegistryError) as excinfo:
        require_entry("V9900099999")
    assert "реестр" in str(excinfo.value)


def test_registry_entry_carries_kind_and_discipline() -> None:
    entry = require_entry(PUE)
    assert (entry.kind, entry.discipline) == ("pue", "electrical")


# --------------------------------------------------------------------- store contract (§3, §4.6)


def _doc(sha: str) -> NormDocument:
    return NormDocument(
        id="V0000000001", title="Тестовый акт", subtitle="Приказ № 1", freshness="Действующий",
        status="in_force", status_note=None, status_conflict=None,
        source_url="https://adilet.zan.kz/rus/docs/V0000000001", content_sha256=sha, meta={},
    )


def _sections(anchors: list[str]) -> list[NormSection]:
    return [
        NormSection(doc_id="V0000000001", anchor=anchor, ordinal=i, breadcrumb="Глава 1.",
                    heading=None, body=f"текст {anchor}", has_table=False)
        for i, anchor in enumerate(anchors)
    ]


def test_store_round_trips_a_document(store: JsonNormStore) -> None:
    outcome = store.upsert_document(
        _doc("sha-a"), _sections(["z1", "z2"]), [LinkEdge(id="V1500010851", anchor="z9")],
        kind="pue", discipline="electrical",
    )
    assert (outcome.changed, outcome.sections_written) == (True, 2)

    record = store.get_document("V0000000001")
    assert record is not None
    assert (record.kind, record.discipline, record.section_count) == ("pue", "electrical", 2)
    assert record.document.status == "in_force"
    assert [s.anchor for s in store.list_sections("V0000000001")] == ["z1", "z2"]
    assert store.get_section("V0000000001", "z2") is not None
    assert store.links_from("V0000000001") == [LinkEdge(id="V1500010851", anchor="z9")]
    assert [r.document.id for r in store.list_documents()] == ["V0000000001"]


def test_upsert_is_idempotent_when_content_is_unchanged(store: JsonNormStore) -> None:
    args = (_doc("sha-a"), _sections(["z1", "z2"]), [])
    store.upsert_document(*args, kind="pue", discipline="electrical")
    second = store.upsert_document(*args, kind="pue", discipline="electrical")
    assert second.changed is False
    assert len(store.list_sections("V0000000001")) == 2


def test_changed_content_replaces_sections_wholesale(store: JsonNormStore) -> None:
    """docs/20 §4.6: смена content_sha256 ⇒ секции перезаписываются ЦЕЛИКОМ.

    Проверяется исчезновение старых якорей, а не только появление новых — иначе тест пройдёт
    и при осиротевших строках, которые поиск вернёт как действующий текст.
    """
    store.upsert_document(_doc("sha-a"), _sections(["z1", "z2", "z3"]), [],
                          kind="pue", discipline="electrical")
    outcome = store.upsert_document(_doc("sha-b"), _sections(["z1", "z9"]), [],
                                    kind="pue", discipline="electrical")
    assert outcome.changed is True
    assert [s.anchor for s in store.list_sections("V0000000001")] == ["z1", "z9"]
    assert store.get_section("V0000000001", "z2") is None
    assert store.get_section("V0000000001", "z3") is None


def test_offline_writes_leave_no_partial_file(store: JsonNormStore, tmp_path: Path) -> None:
    """Атомарная подмена через os.replace — временный файл не остаётся рядом со снапшотом."""
    store.upsert_document(_doc("sha-a"), _sections(["z1"]), [],
                          kind="pue", discipline="electrical")
    root = tmp_path / "norms"
    assert list(root.glob("*.tmp")) == []
    snapshot = json.loads((root / "V0000000001.json").read_text(encoding="utf-8"))
    assert snapshot["document"]["content_sha256"] == "sha-a"


def test_missing_document_reads_empty(store: JsonNormStore) -> None:
    assert store.get_document("V0000000404") is None
    assert store.list_sections("V0000000404") == []
    assert store.links_from("V0000000404") == []


def test_anchorless_sections_are_not_stored(store: JsonNormStore) -> None:
    """Решение заказчика 2026-07-28: не сохранять, но считать (§8.1 запрещает синтетический якорь)."""
    sections = _sections(["z1"]) + [
        NormSection(doc_id="V0000000001", anchor=None, ordinal=1, breadcrumb="",
                    heading=None, body="сноска без якоря", has_table=False)
    ]
    assert len(storable_sections(sections)) == 1
    outcome = store.upsert_document(_doc("sha-a"), sections, [],
                                    kind="pue", discipline="electrical")
    assert outcome.sections_written == 1
    assert all(s.anchor for s in store.list_sections("V0000000001"))


def test_ddl_matches_the_frozen_schema() -> None:
    assert "CREATE TABLE IF NOT EXISTS norm_documents" in NORM_STORE_DDL
    assert "GENERATED ALWAYS AS" in NORM_STORE_DDL and "to_tsvector('russian'" in NORM_STORE_DDL
    assert "norm_sections_tsv_idx" in NORM_STORE_DDL
    # docs/20 §3 объявляет norm_links.anchor nullable и одновременно кладёт его в PRIMARY KEY;
    # в Postgres NULL в PK отвергается, поэтому «нет якоря» хранится пустой строкой.
    assert "anchor   TEXT NOT NULL DEFAULT ''" in NORM_STORE_DDL


def test_links_without_an_anchor_survive_the_round_trip(store: JsonNormStore) -> None:
    store.upsert_document(_doc("sha-a"), _sections(["z1"]),
                          [LinkEdge(id="V1600014771", anchor=None)],
                          kind="pue", discipline="electrical")
    assert store.links_from("V0000000001") == [LinkEdge(id="V1600014771", anchor=None)]


# --------------------------------------------------------------------- ingest (§4)


def test_ingest_writes_a_registry_document(store: JsonNormStore) -> None:
    report = ingest_document(
        TECHREG, store=store, fetcher=fake_fetcher({TECHREG: fixture_html(TECHREG)})
    )
    assert (report.status, report.changed) == ("in_force", True)
    assert report.sections_written > 50
    assert report.links > 0

    record = store.get_document(TECHREG)
    assert record is not None
    assert record.kind == "techreg"
    assert record.document.meta["skipped_anchorless"] == report.sections_skipped
    assert record.section_count == report.sections_written


def test_ingest_refuses_a_document_outside_the_registry(store: JsonNormStore) -> None:
    with pytest.raises(NormRegistryError):
        ingest_document(REPEALED, store=store,
                        fetcher=fake_fetcher({REPEALED: fixture_html(REPEALED)}))
    assert store.list_documents() == []


def test_ingest_refuses_a_contentless_page(store: JsonNormStore) -> None:
    shell = "<html><body>Подтвердите, что Вы не робот</body></html>" + "x" * 30_000
    with pytest.raises(NormIngestError, match="заглушка"):
        ingest_document(TECHREG, store=store, fetcher=fake_fetcher({TECHREG: shell}))
    assert store.list_documents() == []


def test_ingest_fails_loudly_on_a_status_conflict(store: JsonNormStore) -> None:
    """docs/20 §4.4: расхождение сигналов не разрешается молча — падение ДО любой записи."""
    html = fixture_html(TECHREG).replace(
        '<span class="status status_upd">Обновленный</span>', "", 1
    )
    assert parse_document(html, doc_id=TECHREG).document.status == "unknown"
    with pytest.raises(NormIngestError, match="расхождение сигналов статуса"):
        ingest_document(TECHREG, store=store, fetcher=fake_fetcher({TECHREG: html}))
    assert store.list_documents() == []


def test_reingesting_unchanged_html_reports_no_change(store: JsonNormStore) -> None:
    pages = {TECHREG: fixture_html(TECHREG)}
    first = ingest_document(TECHREG, store=store, fetcher=fake_fetcher(pages))
    second = ingest_document(TECHREG, store=store, fetcher=fake_fetcher(pages))
    assert first.changed is True
    assert second.changed is False
    assert store.get_document(TECHREG) is not None
    assert len(store.list_sections(TECHREG)) == first.sections_written


def test_ingest_corpus_walks_the_given_ids(store: JsonNormStore) -> None:
    pages = {PUE: fixture_html(PUE), TECHREG: fixture_html(TECHREG)}
    reports = ingest_corpus([PUE, TECHREG], store=store, fetcher=fake_fetcher(pages))
    assert [r.doc_id for r in reports] == [PUE, TECHREG]
    assert [r.document.id for r in store.list_documents()] == [PUE, TECHREG]
    assert all(r.status == "in_force" for r in reports)


def test_stored_sections_keep_anchors_ordinals_and_tables(store: JsonNormStore) -> None:
    ingest_document(PUE, store=store, fetcher=fake_fetcher({PUE: fixture_html(PUE)}))
    sections = store.list_sections(PUE)
    assert [s.ordinal for s in sections] == sorted(s.ordinal for s in sections)
    assert all(s.anchor and s.id == f"{PUE}#{s.anchor}" for s in sections)
    assert any(s.breadcrumb.startswith("Глава ") for s in sections)
