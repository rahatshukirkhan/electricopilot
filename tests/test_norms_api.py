"""Norm-library search, API and guardrails (docs/20 §6, §7, §8, §12.4).

Offline: the store is the JSON backend under `tmp_path`, injected through
`studio_api._NORM_STORE_OVERRIDE`; the reranker is exercised with `FakeClient`. No network,
no database. The Postgres search path (ts_rank_cd / ts_headline / doc_ids / include_repealed)
cannot be reached without a DB and was verified separately against Neon — see the PR.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from electricopilot import studio_api
from electricopilot.norms.ingest import ingest_document
from electricopilot.norms.parse import NormDocument, NormSection
from electricopilot.norms.search import RERANK_POOL, rerank_hits, search_norms
from electricopilot.norms.store import SNIPPET_START, SNIPPET_STOP, JsonNormStore, SearchHit

from .conftest import FakeClient

FIXTURES = Path(__file__).parent / "fixtures" / "norms"
TECHREG = "V2300032783"
REPEALED_DOC = "V1600014771"  # реестровый ID, здесь помечен repealed синтетически


def fixture_html(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


@pytest.fixture()
def store(tmp_path: Path) -> JsonNormStore:
    created = JsonNormStore(tmp_path / "norms")
    created.init_schema()
    ingest_document(TECHREG, store=created, fetcher=lambda _: fixture_html(TECHREG))
    return created


@pytest.fixture()
def repealed_store(store: JsonNormStore) -> JsonNormStore:
    """Adds a repealed document so §6's include_repealed can be exercised offline."""
    document = NormDocument(
        id=REPEALED_DOC, title="Отменённый акт", subtitle="", freshness="Утративший силу",
        status="repealed", status_note="Утратил силу приказом № 153-НҚ.", status_conflict=None,
        source_url=f"https://adilet.zan.kz/rus/docs/{REPEALED_DOC}",
        content_sha256="sha-repealed", meta={},
    )
    sections = [
        NormSection(doc_id=REPEALED_DOC, anchor="z1", ordinal=0, breadcrumb="Глава 1.",
                    heading=None, body="Требования пожарной безопасности в отменённом акте.",
                    has_table=False)
    ]
    store.upsert_document(document, sections, [], kind="ntd", discipline="electrical")
    return store


@pytest.fixture()
def client(store: JsonNormStore, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(studio_api, "_NORM_STORE_OVERRIDE", store)
    with TestClient(studio_api.app) as test_client:
        yield test_client


def _first_query(store: JsonNormStore) -> str:
    """A phrase guaranteed to be in the fixture, so the search tests can't pass vacuously."""
    return "пожарной безопасности"


# --------------------------------------------------------------------- §6 search (offline)


def test_search_returns_grounded_hits(store: JsonNormStore) -> None:
    hits = search_norms(store, _first_query(store))
    assert hits
    for hit in hits:
        assert hit.anchor and hit.section_id == f"{hit.doc_id}#{hit.anchor}"
        assert hit.source_url.endswith(f"#{hit.anchor}")  # §8.1: цитата ведёт в пункт
        assert hit.doc_status == "in_force"
        assert SNIPPET_START in hit.snippet and SNIPPET_STOP in hit.snippet


def test_search_is_deterministic_and_respects_limit(store: JsonNormStore) -> None:
    first = search_norms(store, _first_query(store), limit=5)
    second = search_norms(store, _first_query(store), limit=5)
    assert [h.section_id for h in first] == [h.section_id for h in second]
    assert len(first) <= 5


def test_search_ranks_are_non_increasing(store: JsonNormStore) -> None:
    ranks = [hit.rank for hit in search_norms(store, _first_query(store), limit=20)]
    assert ranks == sorted(ranks, reverse=True)


def test_empty_query_returns_nothing(store: JsonNormStore) -> None:
    assert search_norms(store, "   ") == []


def test_search_over_an_empty_corpus_is_empty_not_invented(tmp_path: Path) -> None:
    """docs/20 §12.4: пустой корпус ⇒ пустой список, а не выдуманный результат."""
    empty = JsonNormStore(tmp_path / "empty")
    empty.init_schema()
    assert search_norms(empty, "падение напряжения") == []


def test_repealed_sections_are_hidden_by_default(repealed_store: JsonNormStore) -> None:
    query = "пожарной безопасности"
    default_hits = search_norms(repealed_store, query, limit=50)
    assert all(hit.doc_status != "repealed" for hit in default_hits)
    assert all(hit.doc_id != REPEALED_DOC for hit in default_hits)

    with_repealed = search_norms(repealed_store, query, limit=50, include_repealed=True)
    assert any(hit.doc_id == REPEALED_DOC for hit in with_repealed)
    repealed_hit = next(hit for hit in with_repealed if hit.doc_id == REPEALED_DOC)
    assert repealed_hit.doc_status == "repealed"  # §8.4: статус едет вместе с текстом


def test_doc_ids_restricts_the_corpus(repealed_store: JsonNormStore) -> None:
    hits = search_norms(repealed_store, "пожарной безопасности", doc_ids=[REPEALED_DOC],
                        limit=50, include_repealed=True)
    assert hits and {hit.doc_id for hit in hits} == {REPEALED_DOC}


# --------------------------------------------------------------------- §6 rerank


def _hits(count: int) -> list[SearchHit]:
    return [
        SearchHit(section_id=f"D#z{i}", doc_id="D", anchor=f"z{i}", breadcrumb="Глава 1.",
                  heading=None, snippet="фрагмент", rank=1.0 - i / 100, doc_status="in_force",
                  source_url=f"https://adilet.zan.kz/rus/docs/D#z{i}")
        for i in range(count)
    ]


def _rerank(hits: list[SearchHit], response: str) -> list[str]:
    client = FakeClient(response)
    return [h.section_id for h in rerank_hits(hits, query="q", client=client, model="m")]


def test_rerank_applies_a_valid_permutation() -> None:
    hits = _hits(3)
    assert _rerank(hits, '["D#z2", "D#z0", "D#z1"]') == ["D#z2", "D#z0", "D#z1"]


def test_rerank_accepts_a_fenced_json_block() -> None:
    hits = _hits(2)
    assert _rerank(hits, '```json\n["D#z1", "D#z0"]\n```') == ["D#z1", "D#z0"]


@pytest.mark.parametrize(
    "response",
    [
        '["D#z0", "D#z1", "D#zSTRANGE"]',  # неизвестный идентификатор
        '["D#z0", "D#z1"]',  # потерян идентификатор
        '["D#z0", "D#z0", "D#z1"]',  # дубль
        "Вот отсортированный список: D#z2, D#z1, D#z0",  # проза вместо JSON
        '{"order": ["D#z0"]}',  # не массив
        "",  # пустой ответ
        '["D#z0", 17, "D#z2"]',  # не строки
    ],
)
def test_rerank_discards_any_malformed_response(response: str) -> None:
    """docs/20 §6: любой ответ, содержащий что-то кроме известных section_id, отбрасывается
    целиком — порядок остаётся лексическим."""
    hits = _hits(3)
    assert _rerank(hits, response) == [h.section_id for h in hits]


def test_rerank_pool_is_capped_at_thirty(store: JsonNormStore) -> None:
    assert RERANK_POOL == 30
    client = FakeClient("не json")
    hits = search_norms(store, _first_query(store), limit=5, client=client, model="m")
    assert len(hits) <= 5
    assert client.calls == 1  # модель вызвана один раз, поверх лексического топа


def test_rerank_never_invents_a_section(store: JsonNormStore) -> None:
    lexical = search_norms(store, _first_query(store), limit=10)
    reranked = search_norms(store, _first_query(store), limit=10,
                            client=FakeClient("мусор"), model="m")
    assert [h.section_id for h in reranked] == [h.section_id for h in lexical]


# --------------------------------------------------------------------- §7 API


def test_norms_list_endpoint(client: TestClient) -> None:
    response = client.get("/api/norms")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    doc = next(d for d in body["documents"] if d["id"] == TECHREG)
    assert doc["status"] == "in_force" and doc["kind"] == "techreg"
    assert doc["sections"] > 50


def test_document_endpoint_returns_outline_and_readable_page(client: TestClient) -> None:
    """Оглавление — по разделам/главам/параграфам, а не по каждому пункту.

    Регрессия, которую это закрывает: раньше эндпоинт отдавал по записи на секцию, и UI рисовал
    стену из тысяч кодов вида z1938 вместо содержания.
    """
    body = client.get(f"/api/norms/{TECHREG}").json()
    assert body["ok"] is True
    assert body["document"]["status"] == "in_force"  # §8.4 на каждой поверхности

    outline = body["outline"]
    assert outline, "оглавление пустое"
    assert len(outline) < body["document"]["sections"] / 4  # это содержание, а не список пунктов
    assert all(o["text"] and o["anchor"] and o["depth"] >= 0 for o in outline)
    assert any(o["text"].startswith("Глава ") for o in outline)

    page = body["page"]
    assert page["offset"] == 0 and page["sections"]
    assert page["total"] == body["document"]["sections"]
    kinds = {s["kind"] for s in page["sections"]}
    assert kinds <= {"heading", "clause"}
    clauses = [s for s in page["sections"] if s["kind"] == "clause"]
    assert clauses and all(s["body"] for s in clauses)
    assert all(s["source_url"].endswith("#" + s["anchor"]) for s in page["sections"])


def test_document_page_navigates_and_focuses_an_anchor(client: TestClient) -> None:
    first = client.get(f"/api/norms/{TECHREG}?limit=10").json()["page"]
    assert first["prev_offset"] is None and first["next_offset"] == 10

    second = client.get(f"/api/norms/{TECHREG}?offset=10&limit=10").json()["page"]
    assert second["prev_offset"] == 0
    assert {s["anchor"] for s in first["sections"]}.isdisjoint(
        {s["anchor"] for s in second["sections"]}
    )

    # переход из поиска/цитаты: страница открывается на той, где лежит нужный пункт
    target = second["sections"][3]["anchor"]
    focused = client.get(f"/api/norms/{TECHREG}?anchor={target}&limit=10").json()["page"]
    assert focused["offset"] == 10
    assert focused["focus_anchor"] == target
    assert target in {s["anchor"] for s in focused["sections"]}


def test_norms_section_endpoint_carries_citation_and_neighbours(client: TestClient) -> None:
    page = client.get(f"/api/norms/{TECHREG}?limit=20").json()["page"]
    anchors = [s["anchor"] for s in page["sections"]]
    anchor = anchors[1]
    body = client.get(f"/api/norms/{TECHREG}/sections/{anchor}").json()
    section = body["section"]
    assert section["anchor"] == anchor
    assert section["section_id"] == f"{TECHREG}#{anchor}"
    assert section["source_url"] == f"https://adilet.zan.kz/rus/docs/{TECHREG}#{anchor}"
    assert body["prev"]["anchor"] == anchors[0]
    assert body["next"]["anchor"] == anchors[2]
    assert body["document"]["status"] == "in_force"


def test_unknown_document_abstains_with_the_corpus_listing(client: TestClient) -> None:
    """docs/20 §12.4 + §8.2: типизированное 404 со списком того, что есть."""
    response = client.get("/api/norms/V9900099999")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["ok"] is False
    assert detail["error"] == "norm_document_not_found"
    assert TECHREG in [item["id"] for item in detail["available"]]


def test_unknown_anchor_abstains_typed(client: TestClient) -> None:
    response = client.get(f"/api/norms/{TECHREG}/sections/z99999999")
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["error"] == "norm_section_not_found"
    assert detail["document"]["id"] == TECHREG


def test_search_endpoint_returns_grounded_results(client: TestClient) -> None:
    response = client.post("/api/norms/search", json={"query": "пожарной безопасности"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["rerank"] is False
    assert body["results"]
    for hit in body["results"]:
        assert hit["anchor"] and hit["source_url"].endswith(f"#{hit['anchor']}")
        assert hit["doc_status"] in {"in_force", "repealed", "unknown"}


def test_search_endpoint_engages_the_rerank_only_behind_the_flag(
    store: JsonNormStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Флаг NORMS_LLM_RERANK=1 включает сортировку моделью; негодный ответ её отменяет."""
    from dataclasses import replace as replace_dc

    from electricopilot.config import get_config

    live = replace_dc(get_config(), norms_llm_rerank=True, openrouter_api_key="test-key")
    monkeypatch.setattr(studio_api, "get_config", lambda: live)
    monkeypatch.setattr(studio_api, "_NORM_STORE_OVERRIDE", store)
    fake = FakeClient("не json")
    monkeypatch.setattr(studio_api, "_client", lambda: fake)

    with TestClient(studio_api.app) as local:
        body = local.post("/api/norms/search", json={"query": "пожарной безопасности"}).json()
    assert body["rerank"] is True
    assert fake.calls == 1
    lexical = search_norms(store, "пожарной безопасности", limit=20)
    assert [hit["section_id"] for hit in body["results"]] == [h.section_id for h in lexical]


def test_search_endpoint_validates_its_body(client: TestClient) -> None:
    assert client.post("/api/norms/search", json={"query": ""}).status_code == 422
    assert client.post("/api/norms/search", json={"query": "x", "limit": 0}).status_code == 422
    assert client.post("/api/norms/search", json={"query": "x", "limit": 999}).status_code == 422


def test_search_endpoint_on_empty_corpus_returns_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = JsonNormStore(tmp_path / "empty")
    empty.init_schema()
    monkeypatch.setattr(studio_api, "_NORM_STORE_OVERRIDE", empty)
    with TestClient(studio_api.app) as local:
        body = local.post("/api/norms/search", json={"query": "падение напряжения"}).json()
    assert body["ok"] is True and body["results"] == []


def test_norms_api_exposes_no_mutating_routes() -> None:
    """docs/20 §7: ingest — только CLI/офлайн, публичный API читающий."""
    mutating: list[tuple[str, Any]] = []
    for route in studio_api.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path.startswith("/api/norms") and (methods - {"GET", "HEAD", "OPTIONS"}):
            if path != "/api/norms/search":
                mutating.append((path, methods))
    assert mutating == []


def test_norms_endpoints_do_not_require_a_database(
    store: JsonNormStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Библиотека обязана работать офлайн (docs/20 §3) — 503 «no_db» здесь недопустим."""
    monkeypatch.setattr(studio_api, "_NORM_STORE_OVERRIDE", store)
    with TestClient(studio_api.app) as local:
        assert local.get("/api/norms").status_code == 200


# --------------------------------------------------------------------- §10 UI namespace


def test_norm_library_styles_stay_in_their_own_namespace() -> None:
    """Каждый селектор блока библиотеки обязан содержать класс `.nlib-*`.

    Регрессия, которую это ловит: библиотека сначала стилизовала `.norm-panel`, а такой класс
    уже носила панель нормоконтроля. Правила `position: fixed; height: 100vh; z-index: 60`
    превратили её в оверлей поверх всей страницы, и клики по кнопкам щита перехватывались —
    e2e падал таймаутом на «Share-ссылка», где ничего про нормы нет.
    """
    import re

    css = (Path(__file__).parents[1] / "web" / "styles.css").read_text(encoding="utf-8")
    marker = "/* ============ norm library (docs/20 §10) ============ */"
    assert marker in css
    block = re.sub(r"/\*.*?\*/", "", css.split(marker, 1)[1], flags=re.DOTALL)

    offenders = []
    for rule in re.findall(r"^([^{@}][^{}]*)\{", block, re.MULTILINE):
        for selector in rule.split(","):
            selector = selector.strip()
            # шаги @keyframes (`0%`, `from`, `to`) — не селекторы, пространства имён не нарушают
            if re.fullmatch(r"(\d+%|from|to)(\s*,\s*(\d+%|from|to))*", selector):
                continue
            if selector and ".nlib-" not in selector:
                offenders.append(selector)
    assert offenders == [], f"селекторы вне пространства имён библиотеки: {offenders}"


def test_norm_library_does_not_restyle_existing_classes() -> None:
    """Классы, существовавшие до библиотеки, она не переопределяет."""
    import re

    root = Path(__file__).parents[1] / "web"
    css = (root / "styles.css").read_text(encoding="utf-8")
    marker = "/* ============ norm library (docs/20 §10) ============ */"
    head, block = css.split(marker, 1)
    head_rules = re.sub(r"/\*.*?\*/", "", head, flags=re.DOTALL)
    pre_existing = set(re.findall(r"\.([a-z][\w-]*)", head_rules))
    library = set(re.findall(r"\.(nlib-[\w-]*)", block))
    assert not (library & pre_existing)
    # панель нормоконтроля должна остаться обычным блоком в потоке страницы
    assert ".norm-panel{margin-top:16px}" in head
