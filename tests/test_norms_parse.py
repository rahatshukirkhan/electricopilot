"""Norm-library fetch/parse tests (docs/20 §12.1–12.3).

Offline by construction: every case runs against the truncated HTML fixtures in
`tests/fixtures/norms/`; nothing here opens a socket or touches a database.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from electricopilot.norms.fetch import (
    INTEGRITY_MARKER,
    MIN_HTML_BYTES,
    NormFetchError,
    doc_url,
    fetch_cached,
    passes_integrity_gate,
)
from electricopilot.norms.parse import (
    id_from_href,
    looks_contentless,
    parse_body_links,
    parse_doc,
    parse_document,
    parse_history,
    parse_info,
    parse_links,
    source_href,
    table_to_markdown,
)

FIXTURES = Path(__file__).parent / "fixtures" / "norms"
PUE = "V1500010851"  # действующий ПУЭ, бейдж «Обновленный»
REPEALED = "V1900019361"  # СН РК 2019, утратил силу приказом 153-НҚ
TECHREG = "V2300032783"  # действующий техрегламент, короткий
CONFLICT = "SYNTHETIC_STATUS_CONFLICT"  # синтетика: бейдж «Действующий» + сноска об утрате


def read_fixture(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def dump(doc_id: str, html: str) -> str:
    """Canonical JSON dump of a parse, for the §12.2 determinism comparison."""
    parsed = parse_document(html, doc_id=doc_id)
    payload = {
        "document": asdict(parsed.document),
        "sections": [asdict(s) for s in parsed.sections],
        "links": [asdict(link) for link in parsed.links],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


# --------------------------------------------------------------------- fixtures themselves


@pytest.mark.parametrize("name", [PUE, REPEALED, TECHREG])
def test_fixtures_stay_under_the_size_ceiling(name: str) -> None:
    """docs/20 §12.1: ≤ 200 КБ each — regenerate with scripts/make_norm_fixtures.py."""
    assert len(read_fixture(name).encode("utf-8")) <= 200 * 1024


# --------------------------------------------------------------------- §4.2 integrity gate


@pytest.mark.parametrize("name", [PUE, REPEALED, TECHREG])
def test_real_pages_pass_the_integrity_gate(name: str) -> None:
    """The synthetic fixture is intentionally excluded: it is a hand-written page, far below
    the 20 КБ floor the gate uses to tell a real document from an anti-bot shell."""
    assert passes_integrity_gate(read_fixture(name))


def test_integrity_gate_rejects_a_short_response() -> None:
    assert not passes_integrity_gate(f"<html>{INTEGRITY_MARKER} id=\"z1\"</html>")


def test_integrity_gate_rejects_a_page_without_the_marker() -> None:
    padded = "<html>" + ("<p id=\"z1\">текст</p>" * 4000) + "</html>"
    assert len(padded.encode("utf-8")) > MIN_HTML_BYTES
    assert not passes_integrity_gate(padded)


def test_integrity_gate_rejects_a_page_without_z_anchors() -> None:
    padded = "<html>" + INTEGRITY_MARKER + ("<p>текст без якорей</p>" * 4000) + "</html>"
    assert len(padded.encode("utf-8")) > MIN_HTML_BYTES
    assert not passes_integrity_gate(padded)


def test_looks_contentless_refuses_the_captcha_shell() -> None:
    shell = "<html><body><h1>Подтвердите, что Вы не робот</h1>" + ("x" * 30_000) + "</body></html>"
    assert looks_contentless(shell)
    assert not looks_contentless(read_fixture(TECHREG))


def test_fetch_cached_reads_the_cache_without_network(tmp_path: Path) -> None:
    cache = tmp_path / "cached.html"
    cache.write_text("<html>кэш</html>", encoding="utf-8")
    assert fetch_cached("https://adilet.zan.kz/rus/docs/NOPE", cache) == "<html>кэш</html>"


def test_fetch_document_keeps_a_good_cache_when_the_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CAPTCHA shell must never overwrite an already-saved real document (docs/20 §4.2)."""
    from electricopilot.norms import fetch as fetch_mod

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    good = cache_dir / f"{PUE}.html"
    good.write_text("<html>настоящий документ</html>", encoding="utf-8")
    monkeypatch.setattr(fetch_mod, "fetch_url", lambda url, timeout=60.0: "<html>заглушка</html>")

    with pytest.raises(NormFetchError):
        fetch_mod.fetch_document(PUE, cache_dir=cache_dir, refresh=True)
    assert good.read_text(encoding="utf-8") == "<html>настоящий документ</html>"


def test_doc_url_is_canonical() -> None:
    assert doc_url(PUE) == "https://adilet.zan.kz/rus/docs/V1500010851"


# --------------------------------------------------------------------- §12.2 determinism


@pytest.mark.parametrize("name", [PUE, REPEALED, TECHREG])
def test_parser_is_deterministic_across_runs(name: str) -> None:
    """docs/20 §5/§12.2: same input HTML ⇒ identical sections and identical content_sha256."""
    html = read_fixture(name)
    assert dump(name, html) == dump(name, html)


def test_parser_is_deterministic_across_processes() -> None:
    """Ordering must not depend on PYTHONHASHSEED — no bare `set()` may reach the output."""
    script = (
        "import json,sys;from dataclasses import asdict;"
        "sys.path.insert(0,'tests');"
        "from test_norms_parse import dump,read_fixture,CONFLICT;"
        "print(dump(CONFLICT, read_fixture(CONFLICT)))"
    )
    runs = [
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            cwd=Path(__file__).resolve().parent.parent,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout
        for seed in ("0", "12345")
    ]
    assert runs[0] == runs[1]


def test_content_sha256_is_stable_and_input_sensitive() -> None:
    html = read_fixture(TECHREG)
    first = parse_document(html, doc_id=TECHREG).document.content_sha256
    assert first == parse_document(html, doc_id=TECHREG).document.content_sha256
    mutated = html.replace("Область применения", "Область применения (правка)", 1)
    assert parse_document(mutated, doc_id=TECHREG).document.content_sha256 != first


# --------------------------------------------------------------------- §12.3 status


def test_repealed_document_carries_the_verbatim_footnote() -> None:
    doc = parse_document(read_fixture(REPEALED), doc_id=REPEALED).document
    assert doc.freshness == "Утративший силу"
    assert doc.status == "repealed"
    assert doc.status_conflict is None
    assert doc.status_note is not None
    assert doc.status_note.startswith("Утратил силу приказом")
    assert "153-НҚ" in doc.status_note  # чем именно отменён — инженеру это и нужно


def test_short_live_document_is_in_force() -> None:
    doc = parse_document(read_fixture(TECHREG), doc_id=TECHREG).document
    assert (doc.freshness, doc.status, doc.status_note) == ("Обновленный", "in_force", None)


def test_live_pue_is_in_force_despite_page_wide_repeal_language() -> None:
    """docs/20 §12.3: the badge decides; the footnote is looked for in the header ONLY.

    A page-wide substring search is the failure this case guards against — a live norm shown
    as repealed, or (worse, §8.4) a repealed one shown as live.
    """
    doc = parse_document(read_fixture(PUE), doc_id=PUE).document
    assert (doc.freshness, doc.status, doc.status_note) == ("Обновленный", "in_force", None)


def test_header_search_ignores_repeal_language_outside_the_header() -> None:
    """Explicit teeth for §4.4 ⚠: the live-ПУЭ fixture carries no repeal wording of its own
    (verified on the full 3.7 MB page — it lives on the separate /links tab), so the bait is
    injected into the body and into a «Ссылки на документ» block here."""
    html = read_fixture(PUE)
    bait = (
        '<div id="to"><p>Сноска. Пункт 5 утратил силу приказом Министра энергетики РК '
        'от 01.01.2024 № 7.</p><p>Утратила силу совместным приказом.</p></div>'
    )
    injected = html.replace("</article>", bait + "</article>", 1)
    assert "утратил силу" in injected.lower()
    doc = parse_document(injected, doc_id=PUE).document
    assert doc.status == "in_force"
    assert doc.status_note is None


def test_conflicting_signals_yield_unknown_instead_of_a_silent_choice() -> None:
    """docs/20 §4.4: badge in_force + repeal footnote in the header is NOT resolved in code."""
    doc = parse_document(read_fixture(CONFLICT), doc_id="V2000000001").document
    assert doc.freshness == "Действующий"
    assert doc.status == "unknown"
    assert doc.status_conflict is not None
    assert doc.status_note is not None and doc.status_note.startswith("Утратил силу приказом")


def test_repealed_badge_without_a_footnote_is_also_a_conflict() -> None:
    """The «или наоборот» half of §4.4 — confirmed with the customer 2026-07-28."""
    html = read_fixture(REPEALED)
    stripped = html.replace(
        " Утратил силу приказом и.о. Председателя Комитета по делам строительства "
        "и жилищно-коммунального хозяйства Министерства промышленности и строительства "
        "Республики Казахстан от 18 октября 2023 года № 153-НҚ.",
        "",
        1,
    )
    assert stripped != html
    doc = parse_document(stripped, doc_id=REPEALED).document
    assert doc.freshness == "Утративший силу"
    assert doc.status == "unknown"
    assert doc.status_conflict is not None


def test_missing_badge_is_unknown() -> None:
    html = read_fixture(TECHREG).replace('<span class="status status_upd">Обновленный</span>', "", 1)
    doc = parse_document(html, doc_id=TECHREG).document
    assert doc.freshness is None
    assert doc.status == "unknown"


# --------------------------------------------------------------------- sections & links


def test_sections_are_anchored_ordered_and_breadcrumbed() -> None:
    parsed = parse_document(read_fixture(TECHREG), doc_id=TECHREG)
    assert parsed.sections
    assert [s.ordinal for s in parsed.sections] == list(range(len(parsed.sections)))

    anchored = [s for s in parsed.sections if s.anchor]
    assert len(anchored) > 50
    assert all(s.anchor is not None and s.anchor.startswith("z") for s in anchored)
    assert all(s.id == f"{TECHREG}#{s.anchor}" for s in anchored)
    # anchorless sections exist (preamble/notes) and must NOT get a synthesised id (§8.1)
    assert all(s.id is None for s in parsed.sections if s.anchor is None)

    crumbs = {s.breadcrumb for s in parsed.sections}
    assert any(c.startswith("Глава 1.") for c in crumbs)
    assert any(" › Параграф " in c for c in crumbs)


def test_tables_are_kept_as_pipe_text_without_markup() -> None:
    parsed = parse_document(read_fixture(REPEALED), doc_id=REPEALED)
    tables = [s for s in parsed.sections if s.has_table]
    assert tables
    body = "\n".join(s.body for s in tables)
    assert "|" in body and " --- " in body
    assert "<td" not in body and "<table" not in body


def test_body_links_are_extracted_in_document_order() -> None:
    links = parse_body_links(read_fixture(CONFLICT))
    assert [(link.id, link.anchor) for link in links] == [("V1500010851", "z1204")]
    # the same edges come out of parse_document, so the graph does not depend on the entry point
    assert parse_document(read_fixture(CONFLICT), doc_id="V2000000001").links == links


def test_document_links_of_a_real_page_are_deduped_and_ordered() -> None:
    links = parse_body_links(read_fixture(PUE))
    keys = [(link.id, link.anchor) for link in links]
    assert keys
    assert len(keys) == len(set(keys))
    assert all(link.id[0].isupper() for link in links)


# --------------------------------------------------------------------- ported helpers


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/rus/docs/V1500010851#z1204", "V1500010851"),
        ("https://adilet.zan.kz/rus/docs/K1400000226", "K1400000226"),
        ("/rus/docs/Z010000148_", "Z010000148_"),  # legacy trailing underscore is part of the id
        ("/rus/docs/V24IJ012419", "V24IJ012419"),  # new (~2020+) mixed-alphanumeric family
        ("/rus/docs/dt=2024", ""),  # listing chrome, lowercase — not a document
        ("/rus/docs/rss", ""),
        (None, ""),
    ],
)
def test_id_from_href(href: str | None, expected: str) -> None:
    assert id_from_href(href) == expected


def test_source_href_only_deep_links_real_anchors() -> None:
    url = doc_url(PUE)
    assert source_href(url, "z1204") == f"{url}#z1204"
    assert source_href(url, None) == url
    # synthesised chunk anchors do not exist on the page and must not become a #fragment
    assert source_href(url, "c17") == url
    assert source_href(url, "p3") == url


def test_digit_sup_sub_becomes_unicode_before_text_extraction() -> None:
    html = (
        "<article><p id='z1'>площадь 5 м<sup>3</sup> и ставка кредитования<sup>17</sup>, "
        "формула H<sub>2</sub>O, а также <sup>прим.</sup></p>"
        + "<p>" + "заполнение " * 40 + "</p></article>"
    )
    text = parse_doc(html).sections[0].text
    assert "м³" in text
    assert "кредитования¹⁷" in text
    assert "H₂O" in text
    assert "прим." in text  # non-digit sup/sub left untouched


def test_table_to_markdown_expands_colspan_and_rowspan() -> None:
    from electricopilot.norms.parse import _make_soup

    html = """
    <table id="z9">
      <tr><th rowspan="2">Сечение</th><th colspan="2">Ток, А</th></tr>
      <tr><th>откр.</th><th>в трубе</th></tr>
      <tr><td>1,5</td><td>23</td><td>19</td></tr>
    </table>
    """
    table = _make_soup(html).find("table")
    assert table is not None
    result = table_to_markdown(table)
    assert result is not None
    anchor, md = result
    assert anchor == "z9"
    rows = md.splitlines()
    assert rows[0] == "| Сечение | Ток, А |  |"  # colspan lands in the first spanned column
    assert rows[1] == "| --- | --- | --- |"
    assert rows[2] == "| Сечение | откр. | в трубе |"  # rowspan carried into the next row
    assert rows[3] == "| 1,5 | 23 | 19 |"


def test_layout_table_falls_back_to_recursion_into_its_cells() -> None:
    """A one-column layout table must not swallow its text (docs/20 §4, tableToMarkdown)."""
    html = (
        "<article><table><tr><td><p id='z5'>Приложение 1 к Правилам</p></td></tr>"
        "<tr><td><p id='z6'>Технические данные</p></td></tr></table>"
        "<p>" + "заполнение " * 40 + "</p></article>"
    )
    sections = parse_doc(html).sections
    texts = [s.text for s in sections]
    assert "Приложение 1 к Правилам" in texts
    assert "Технические данные" in texts
    assert all(s.tag != "table" for s in sections)


def test_parse_info_reads_the_key_value_table() -> None:
    html = (
        "<table><tr><td>Тип документа</td><td>Приказ</td></tr>"
        "<tr><td>Орган</td><td>Министерство\n энергетики</td></tr>"
        "<tr><td>одна ячейка</td></tr></table>"
    )
    assert parse_info(html) == {"Тип документа": "Приказ", "Орган": "Министерство энергетики"}


def test_parse_history_collects_dates_and_amending_acts() -> None:
    html = (
        "<table><tr><td>18.10.2023</td>"
        "<td><a href='/rus/docs/V2300033579#z24'>№ 153-НҚ</a></td></tr>"
        "<tr><td>без даты</td><td>шум</td></tr></table>"
    )
    entries = parse_history(html)
    assert len(entries) == 1
    assert entries[0].date == "18.10.2023"
    assert entries[0].refs == ["V2300033579"]


def test_parse_links_splits_incoming_and_outgoing() -> None:
    html = (
        "<div id='from'><a href='/rus/docs/V2300033579#z24'>a</a>"
        "<a href='/rus/docs/V2300033579#z24'>дубль</a></div>"
        "<div id='to'><a href='/rus/docs/K1400000226'>b</a></div>"
    )
    links = parse_links(html)
    assert [(e.id, e.anchor) for e in links.from_doc] == [("V2300033579", "z24")]
    assert [(e.id, e.anchor) for e in links.to_doc] == [("K1400000226", None)]
