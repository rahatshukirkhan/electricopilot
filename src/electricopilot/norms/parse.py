"""adilet.zan.kz HTML → norm sections (docs/20 §4.3–4.5).

Port of `~/vibecode/legality/lib/adilet.ts` + `lib/parse-html.ts` (shared repo, порт разрешён
docs/20 §2), cheerio → BeautifulSoup. legality's parser is battle-tested on 2 727 documents, so
it — not the narrower `scripts/extract_pue_rk.py` — is the reference for adilet parsing; the
"why" comments are carried over with the code so the same rakes are not stepped on twice.

NOT ported (docs/20 §1.4, §4): `enumerateYear*` and everything that talks to `/rus/search/` or
`/rus/list/docs/` — those paths are disallowed by adilet's robots.txt.

Determinism (docs/20 §5): one pinned parser backend, NFC normalisation, and ordered containers
everywhere (`seen` set + list, never a bare `set()` in output) so a JSON dump of the result is
byte-identical across runs and processes regardless of PYTHONHASHSEED.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from bs4 import BeautifulSoup, Tag

# ONE pinned backend, because a parser swap silently changes section boundaries and would break
# the §5 determinism invariant. html5lib specifically:
#   * it follows the HTML5 tree-construction spec, the same family as cheerio/parse5 in
#     legality — which is what makes the §12.8 port comparison meaningful;
#   * adilet nests <table> inside <p>, and bs4's html.parser mangles that badly enough to lose
#     ALL 250 tables of the ПУЭ (measured: 250 → 0), while html5lib recovers them correctly;
#   * lxml parses identically (byte-identical content_sha256 on the full 3.7 МБ ПУЭ page) but
#     merely INSTALLING it flips openpyxl to its lxml XML backend, which then rejects the
#     core.xml our byte-deterministic xlsx normalizer rewrites (PER-14). So: not lxml.
_PARSER = "html5lib"

# Real fragment ids on the live adilet page are `z\d+`. legality's chunker SYNTHESIZES other
# anchor shapes ("cN", "pN") for sections that have no source anchor, and those do NOT exist as
# ids on the actual page (DB-verified there: 106 976 z-anchors vs 24 780 "cN" + 4 688 "pN").
# So only ever append a `#fragment` when the anchor is a real z-anchor; otherwise link to the
# bare document URL rather than invent a fragment that would 404-scroll on adilet.
REAL_ANCHOR_RE = re.compile(r"^z\d+$")

_HEADING_RE = re.compile(r"^h[1-4]$")
_ANCHOR_PREFIX_RE = re.compile(r"^z\d+")
GRID_CELL_MAX = 800  # a cell longer than this ⇒ layout table, not a data grid

NormStatus = Literal["in_force", "repealed", "unknown"]


# ---------------------------------------------------------------- normalisation


def normalize_text(value: str) -> str:
    """NFC + nbsp→space + whitespace collapse (docs/20 §5)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).replace("\xa0", " ")).strip()


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, _PARSER)


def _element_children(node: Tag) -> list[Tag]:
    """Direct *element* children, in document order (cheerio's `.children()`)."""
    return [child for child in node.find_all(recursive=False) if isinstance(child, Tag)]


def _attr(el: Tag, name: str) -> str | None:
    value = el.get(name)
    if isinstance(value, list):  # bs4 splits multi-valued attrs (class); ids/names are scalar
        value = " ".join(value)
    return value


# ---------------------------------------------------------------- sup/sub

_SUPERSCRIPT_DIGITS = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
}  # fmt: skip
_SUBSCRIPT_DIGITS = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
}  # fmt: skip


def convert_digit_sup_sub(root: Tag) -> None:
    """Replace digit-only ``<sup>``/``<sub>`` with Unicode super/subscript, in place.

    Call ONCE per document, right after the body container is selected and BEFORE any text
    extraction: it fixes footnote-ref gluing (``кредитования<sup>17</sup>`` → «кредитования¹⁷»,
    still glued but now unambiguous) while keeping units correct (``м<sup>3</sup>`` → «м³»).
    In norm texts either failure is direct corruption of meaning. Non-digit sup/sub is left
    untouched (rare; stays glued exactly as before).
    """
    for el in root.find_all(["sup", "sub"]):
        if not isinstance(el, Tag):
            continue
        txt = el.get_text().strip()
        if not txt.isdigit() or not txt.isascii():
            continue  # non-digit (or empty) sup/sub: leave glued as-is
        table = _SUBSCRIPT_DIGITS if el.name == "sub" else _SUPERSCRIPT_DIGITS
        el.replace_with("".join(table.get(ch, ch) for ch in txt))


# ---------------------------------------------------------------- text extraction


def block_text(el: Tag) -> str:
    """Inner text of a block, preserving ``<br>`` as newlines and nbsp as space."""
    raw = el.decode_contents()
    with_breaks = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    # Fragment-parse the replaced markup instead of mutating the source tree — the ordered walk
    # in `parse_doc` keeps iterating over the original nodes and must not be disturbed.
    text = unicodedata.normalize("NFC", _make_soup(f"<div>{with_breaks}</div>").get_text())
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _cell_text(el: Tag) -> str:
    """One cell's text, single-line (newlines→space), pipe-escaped for markdown."""
    return re.sub(r"\n+", " ", block_text(el)).replace("|", r"\|").strip()


def first_anchor(el: Tag) -> str | None:
    """First ``z\\d+``-shaped anchor on an element: its own id, or a descendant ``<a name=z…>``."""
    el_id = _attr(el, "id")
    if el_id and _ANCHOR_PREFIX_RE.match(el_id):
        return el_id
    named = el.select_one("a[name^='z']")
    if named is not None:
        name = _attr(named, "name")
        if name and _ANCHOR_PREFIX_RE.match(name):
            return name
    return None


# ---------------------------------------------------------------- tables


@dataclass(frozen=True)
class _CellShape:
    colspan: int
    rowspan: int


@dataclass(frozen=True)
class _RawCell:
    text: str
    colspan: int
    rowspan: int


def _span(el: Tag, name: str) -> int:
    raw = _attr(el, name) or "1"
    match = re.match(r"\s*(\d+)", raw)
    return max(1, int(match.group(1))) if match else 1


def _row_cells(tr: Tag) -> list[Tag]:
    return [c for c in tr.find_all(["td", "th"], recursive=False) if isinstance(c, Tag)]


def _grid_width(raw_rows: list[list[_CellShape]]) -> int:
    """Shape-only column walk (no cell text), used to reject a <2-column layout table BEFORE
    the expensive per-cell text extraction. Column trimming only ever REMOVES columns from the
    final grid, so this raw pre-trim width is an exact upper bound on the final column count.
    """
    width = 0
    pending: dict[int, int] = {}  # remaining rowspan owed per column
    for raw in raw_rows:
        col = 0
        i = 0
        while i < len(raw) or pending.get(col, 0) > 0:
            if pending.get(col, 0) > 0:
                pending[col] -= 1
                col += 1
                continue
            cell = raw[i]
            i += 1
            for _ in range(cell.colspan):
                pending[col] = cell.rowspan - 1 if cell.rowspan > 1 else 0
                col += 1
        width = max(width, col)
    return width


def _expand_grid(raw_rows: list[list[_RawCell]]) -> list[list[str]]:
    """Expand per-row cell lists (with colspan/rowspan) into a full rectangular grid.

    Every output row gets one entry per grid column: a rowspan value is carried down into every
    continuation row it covers, and a colspan value lands in the FIRST spanned column with the
    remaining spanned columns padded empty rather than repeated — so the grid never lies about
    how many independent values a row actually has.
    """
    grid: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}  # col -> (text, rows remaining)
    for raw in raw_rows:
        row: dict[int, str] = {}
        col = 0
        i = 0
        while i < len(raw) or pending.get(col, ("", 0))[1] > 0:
            carried = pending.get(col)
            if carried is not None and carried[1] > 0:
                row[col] = carried[0]
                pending[col] = (carried[0], carried[1] - 1)
                col += 1
                continue
            cell = raw[i]
            i += 1
            for k in range(cell.colspan):
                val = cell.text if k == 0 else ""
                row[col] = val
                pending[col] = (val, cell.rowspan - 1) if cell.rowspan > 1 else ("", 0)
                col += 1
        width = max(row) + 1 if row else 0
        grid.append([row.get(c, "") for c in range(width)])
    return grid


def table_to_markdown(table: Tag) -> tuple[str | None, str] | None:
    """Convert a ``<table>`` to a markdown grid, or ``None`` if it's a layout table."""
    trs = [tr for tr in table.find_all("tr") if isinstance(tr, Tag)]
    # Cheap layout-table rejection first (row/column trimming below only ever REMOVES rows and
    # columns, so these raw pre-extraction counts are exact upper bounds) — this skips the
    # per-cell text extraction entirely for tables that could never pass anyway.
    if len(trs) < 2:
        return None
    shapes = [[_CellShape(_span(c, "colspan"), _span(c, "rowspan")) for c in _row_cells(tr)]
              for tr in trs]
    if _grid_width(shapes) < 2:
        return None

    raw_rows = [
        [_RawCell(_cell_text(c), _span(c, "colspan"), _span(c, "rowspan")) for c in _row_cells(tr)]
        for tr in trs
    ]
    rows = _expand_grid(raw_rows)
    width = max((len(r) for r in rows), default=0)

    # drop columns empty in every row, and rows empty in every column (layout scaffolding)
    kept_cols = [c for c in range(width) if any((r[c] if c < len(r) else "").strip() for r in rows)]
    rows = [[(r[c] if c < len(r) else "") for c in kept_cols] for r in rows]
    rows = [r for r in rows if any(c.strip() for c in r)]

    n_cols = len(kept_cols)
    max_cell = max((len(c) for r in rows for c in r), default=0)
    if len(rows) < 2 or n_cols < 2 or max_cell > GRID_CELL_MAX:
        return None  # layout / giant-cell / 1-D

    def line(row: list[str]) -> str:
        padded = row + [""] * (n_cols - len(row))
        return "| " + " | ".join(padded) + " |"

    sep = "|" + " --- |" * n_cols
    md = "\n".join([line(rows[0]), sep, *(line(r) for r in rows[1:])])
    return first_anchor(table), md


# ---------------------------------------------------------------- document parsing


@dataclass(frozen=True)
class Section:
    """One emitted body element, in document order (legality's `Section`)."""

    anchor: str | None
    tag: str
    text: str


@dataclass(frozen=True)
class ParsedDoc:
    title: str
    subtitle: str
    freshness: str | None  # «Обновленный» | «Новый» | «Утративший силу» | «Действующий»
    sections: list[Section]
    markdown: str


_FRESHNESS_RE = re.compile(
    r"^(Обновл[её]нн(?:ый|ое|ая)|Нов(?:ый|ое|ая)|Утративший силу|Действующий)"
)


def looks_contentless(html: str) -> bool:
    """True if an adilet doc page carries no legal text.

    legality's bulk re-fetch tripped adilet's anti-bot wall and got back an identical
    20623-byte «Подтвердите, что Вы не робот» CAPTCHA shell for 43% of documents — which the
    old `length > 200` guard happily stored as a "document". Detect it so such a page is NEVER
    persisted (and so a future ingest fails loudly instead of silently indexing empty docs).
    This REFUSES the shell; it does not attempt to solve or bypass the CAPTCHA.
    """
    if not html or len(html) < 200:
        return True
    if re.search(
        r"Подтвердите,?\s*что\s*Вы\s*не\s*робот|cf-browser-verification|g-recaptcha|hcaptcha",
        html,
        re.IGNORECASE,
    ):
        return True
    body = _select_body(_make_soup(html))
    if body is None:
        return True
    return len(re.sub(r"\s+", " ", body.get_text()).strip()) < 500


def _select_body(soup: BeautifulSoup) -> Tag | None:
    """Pick the body container with the most text: `<article>`, falling back to `.main`."""
    article = soup.find("article")
    if isinstance(article, Tag) and len(article.get_text().strip()) >= 200:
        return article
    main = soup.select_one(".main")
    if isinstance(main, Tag):
        return main
    return article if isinstance(article, Tag) else None


def _clean_body(body: Tag) -> None:
    for junk in body.select("script,style,noscript,.social,.skip,nav"):
        junk.decompose()
    convert_digit_sup_sub(body)


def _walk(node: Tag, out: list[Section]) -> None:
    """Ordered walk: headings/paragraphs/list items are emitted with the same plain text
    extraction legality has always used (element `.text`, not `block_text` — that is what keeps
    non-table output identical to the old flat-selector pass); `<table>` is walked in document
    order and emitted as ONE markdown-table section, so its cells are not ALSO picked up by the
    p/li pass. A table that `table_to_markdown` rejects as layout (rows<2, cols<2, giant cell)
    is recursed into instead, so its cell text still surfaces as ordinary paragraphs.
    """
    for child in _element_children(node):
        tag = (child.name or "").lower()
        if not tag:
            continue
        if _HEADING_RE.match(tag) or tag in ("p", "li"):
            text = normalize_text(child.get_text())
            if text:
                out.append(Section(anchor=first_anchor(child), tag=tag, text=text))
        elif tag == "table":
            md = table_to_markdown(child)
            if md is not None:
                out.append(Section(anchor=md[0], tag="table", text=md[1]))
            else:
                _walk(child, out)  # layout table → recurse into its cells
        else:
            _walk(child, out)  # div / section / tbody / tr / td / ul / ol …


def _parse_doc_soup(soup: BeautifulSoup) -> tuple[ParsedDoc, Tag | None]:
    h1 = soup.find("h1")
    title = normalize_text(h1.get_text()) if isinstance(h1, Tag) else ""

    # subtitle = the act citation line («Приказ … от … № …. Зарегистрирован …»), minus the UI
    # freshness badge that adilet renders as an element between <h1> and that line.
    subtitle = ""
    if isinstance(h1, Tag):
        siblings = [s for s in h1.next_siblings if isinstance(s, Tag)][:3]
        subtitle = normalize_text("".join(s.get_text() for s in siblings))[:300]
    match = _FRESHNESS_RE.match(subtitle)
    freshness = match.group(1) if match else None
    if freshness:
        subtitle = subtitle[len(freshness):].strip()

    body = _select_body(soup)
    sections: list[Section] = []
    if body is not None:
        _clean_body(body)
        _walk(body, sections)

    md = (
        f"# {title}\n\n"
        + (f"_{subtitle}_\n\n" if subtitle else "")
        + "\n\n".join(
            f"\n## {s.text}\n" if _HEADING_RE.match(s.tag) else s.text for s in sections
        )
    )
    return ParsedDoc(title, subtitle, freshness, sections, md), body


def parse_doc(html: str) -> ParsedDoc:
    """Parse a `/rus/docs/{ID}` page: title, subtitle, freshness badge and ordered sections."""
    return _parse_doc_soup(_make_soup(html))[0]


# ---------------------------------------------------------------- info / history / links

DocMeta = dict[str, str]


def parse_info(html: str) -> DocMeta:
    """Parse the `/info` key-value table into a flat metadata object."""
    meta: DocMeta = {}
    for tr in _make_soup(html).find_all("tr"):
        if not isinstance(tr, Tag):
            continue
        tds = [td for td in tr.find_all("td", recursive=False) if isinstance(td, Tag)]
        if len(tds) == 2:
            key = tds[0].get_text().strip()
            if key:
                meta[key] = normalize_text(tds[1].get_text())
    return meta


@dataclass(frozen=True)
class HistoryEntry:
    date: str
    note: str
    refs: list[str]


_DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")


def parse_history(html: str) -> list[HistoryEntry]:
    """Parse `/history` into amendment entries (dates + amending-act references)."""
    out: list[HistoryEntry] = []
    seen: set[str] = set()
    for el in _make_soup(html).select("table tr, li"):
        text = normalize_text(el.get_text())
        date_match = _DATE_RE.search(text)
        if not date_match:
            continue
        refs: list[str] = []
        ref_seen: set[str] = set()
        for a in el.select("a[href*='/rus/docs/']"):
            doc_id = id_from_href(_attr(a, "href"))
            if doc_id and doc_id not in ref_seen:
                ref_seen.add(doc_id)
                refs.append(doc_id)
        entry = HistoryEntry(date=date_match.group(0), note=text[:240], refs=refs)
        key = entry.date + entry.note
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


@dataclass(frozen=True)
class LinkEdge:
    id: str
    anchor: str | None


@dataclass(frozen=True)
class DocLinks:
    from_doc: list[LinkEdge]  # outgoing (`#from` on the /links page)
    to_doc: list[LinkEdge]  # incoming (`#to`)


_HREF_ANCHOR_RE = re.compile(r"#(z\d+)")


def _grab_edges(root: Tag | BeautifulSoup, selector: str) -> list[LinkEdge]:
    edges: list[LinkEdge] = []
    seen: set[str] = set()  # dedup key only — output order stays document order (determinism)
    for a in root.select(selector):
        href = _attr(a, "href") or ""
        doc_id = id_from_href(href)
        if not doc_id:
            continue
        anchor_match = _HREF_ANCHOR_RE.search(href)
        anchor = anchor_match.group(1) if anchor_match else None
        key = doc_id + (anchor or "")
        if key in seen:
            continue
        seen.add(key)
        edges.append(LinkEdge(id=doc_id, anchor=anchor))
    return edges


def parse_links(html: str) -> DocLinks:
    """Parse the `/links` page into the citation graph: outgoing / incoming."""
    soup = _make_soup(html)
    return DocLinks(
        from_doc=_grab_edges(soup, "#from a[href*='/rus/docs/']"),
        to_doc=_grab_edges(soup, "#to a[href*='/rus/docs/']"),
    )


def parse_body_links(html: str) -> list[LinkEdge]:
    """All `/rus/docs/{ID}` references inside the document body (docs/20 §4.5).

    This is the legal way to resolve editions (docs/20 §1.2): the current act is reached
    through a link from the repealed one, never through adilet's search or from model memory.
    """
    body = _select_body(_make_soup(html))
    if body is None:
        return []
    _clean_body(body)
    return _grab_edges(body, "a[href*='/rus/docs/']")


def id_from_href(href: str | None) -> str:
    """Extract an adilet document id from an href.

    Two id families must both be accepted while the non-doc `/docs/{dt,rss,sort_field}` paths
    that also appear in page chrome are rejected — those are lowercase, so requiring a leading
    `[A-Z]` alone rejects them:
      - classic: letter + 9-10 digits (`K1400000226`, `V1500012590`), older ids carrying a
        literal trailing underscore (`Z010000148_`) that is kept as part of the id;
      - new (~2020+): letter + 2-digit year + a short run of mixed letters/digits
        (`V24IJ012419`, `G25AAZ3734M`) — a digits-only regex silently dropped all of these.
    Every real id is 10-11 chars before an optional trailing `_`, mixing only uppercase
    letters/digits, so one bounded alphanumeric run covers both families.
    """
    if not href:
        return ""
    match = re.search(r"/docs/([A-Z][A-Z0-9]{9,10}_?)", href)
    return match.group(1) if match else ""


def source_href(url: str, anchor: str | None = None) -> str:
    """Build a source-page URL, deep-linked only when `anchor` is a real z-anchor."""
    return f"{url}#{anchor}" if anchor and REAL_ANCHOR_RE.match(anchor) else url


# ---------------------------------------------------------------- status (docs/20 §4.4)

_BADGE_REPEALED = "Утративший силу"
_BADGE_IN_FORCE = frozenset({"Обновленный", "Обновлённый", "Новый", "Действующий"})

# Verb forms only («Утратил/Утратила/Утратило/Утратили силу»). The participle «Утративший силу»
# is the badge itself and, in body text, the ordinary way a live act refers to acts it repealed
# («о признании утратившими силу…») — matching it here would flip live norms to `repealed`.
_REPEAL_NOTE_RE = re.compile(r"Утратил[аои]?\s+силу")


@dataclass(frozen=True)
class NormDocument:
    id: str
    title: str
    subtitle: str
    freshness: str | None
    status: NormStatus
    status_note: str | None
    status_conflict: str | None
    source_url: str
    content_sha256: str
    meta: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NormSection:
    doc_id: str
    anchor: str | None
    ordinal: int
    breadcrumb: str
    heading: str | None
    body: str
    has_table: bool

    @property
    def id(self) -> str | None:
        """`'{doc_id}#{anchor}'`, or None for a section the source gave no anchor.

        An anchorless section cannot be shown as a citation (docs/20 §8.1) and must never get a
        synthesised anchor — see REAL_ANCHOR_RE.
        """
        return f"{self.doc_id}#{self.anchor}" if self.anchor else None


@dataclass(frozen=True)
class ParsedNorm:
    document: NormDocument
    sections: list[NormSection]
    links: list[LinkEdge]


def _header_region_text(soup: BeautifulSoup) -> str:
    """Text of the document header ONLY — from `<h1>` down to the tab row (docs/20 §4.4 ⚠).

    Searching the whole page for «Утратил силу» produces false positives: a live act cites
    repealed acts in its body and in its «Ссылки на документ» block. Getting this wrong in one
    direction marks a live norm as repealed; in the other it shows an engineer a repealed
    document as current — which is the very harm the status field exists to prevent (§8.4).

    The freshness badge (`span.status`) is dropped before the search: its own text is
    «Утративший силу», which would otherwise self-confirm as a footnote for every repealed doc.
    """
    h1 = soup.find("h1")
    if not isinstance(h1, Tag):
        return ""
    parent = h1.parent
    if isinstance(parent, Tag) and "Официальная публикация" not in parent.get_text():
        nodes: list[Tag] = [parent]
    else:
        # Parent swallowed the tab row — walk h1's own siblings and stop at the tabs container.
        nodes = []
        for sib in h1.next_siblings:
            if not isinstance(sib, Tag):
                continue
            if _attr(sib, "id") == "tabs_container" or "tabs" in (_attr(sib, "class") or ""):
                break
            nodes.append(sib)
    parts: list[str] = []
    for node in nodes:
        clone = _make_soup(str(node))
        for badge in clone.select("span.status, h1"):
            badge.decompose()
        parts.append(clone.get_text())
    return normalize_text(" ".join(parts))


def _resolve_status(freshness: str | None, header_text: str) -> tuple[NormStatus, str | None, str | None]:
    """Badge-first status with the header footnote as an independent second signal (§4.4).

    The badge is adilet's own markup, not our heuristic. The footnote («Утратил силу приказом …»
    in the header) is quoted verbatim into `status_note` because an engineer needs to know what
    exactly repealed the document. The two signals disagreeing is NOT resolved silently: status
    becomes `unknown` and ingest (PR 2) fails loudly so a human looks. No date heuristics.
    """
    note_match = _REPEAL_NOTE_RE.search(header_text)
    note = header_text[note_match.start():].strip() if note_match else None

    if freshness is None:
        return "unknown", note, "бейдж свежести не найден в шапке документа"
    if freshness == _BADGE_REPEALED:
        if note is None:
            return "unknown", None, "бейдж «Утративший силу», но сноски об утрате силы в шапке нет"
        return "repealed", note, None
    if freshness in _BADGE_IN_FORCE:
        if note is not None:
            return "unknown", note, f"бейдж «{freshness}», но в шапке сноска об утрате силы"
        return "in_force", None, None
    return "unknown", note, f"неизвестный бейдж свежести «{freshness}»"


# «Глава N.» / «Параграф N.» / «Приложение N» headings drive the breadcrumb (docs/20 §4.3).
# Note: adilet renders «Приложение N» inside a layout <table> cell, not as a heading, so in
# practice only Глава/Параграф fire — see the PR notes.
_CHAPTER_RE = re.compile(r"^(?:Глава|Приложение)\s+\d+")
_PARAGRAPH_RE = re.compile(r"^Параграф\s+\d+")


def parse_document(html: str, *, doc_id: str, source_url: str | None = None) -> ParsedNorm:
    """HTML → `NormDocument` + ordered `NormSection`s + body link graph (docs/20 §4.3–4.5).

    Sections are split on z-anchors (the source puts them on clauses); anchorless elements
    accumulate into the section opened by the last anchor. Breadcrumbs come from the nearest
    preceding «Глава»/«Параграф» headings. Tables are kept as pipe-separated text with
    `has_table=True`; no HTML markup is carried into the store.

    Returns rather than raises on a status conflict: the caller (ingest, PR 2) is what fails
    loudly, so the document stays inspectable here.
    """
    soup = _make_soup(html)
    header_text = _header_region_text(soup)  # before _parse_doc_soup mutates the tree
    parsed, body = _parse_doc_soup(soup)
    status, status_note, conflict = _resolve_status(parsed.freshness, header_text)

    sections: list[NormSection] = []
    chapter: str | None = None
    paragraph: str | None = None
    started = False
    open_anchor: str | None = None
    open_heading: str | None = None
    open_breadcrumb = ""
    open_parts: list[str] = []
    open_has_table = False

    def flush() -> None:
        nonlocal started, open_anchor, open_heading, open_parts, open_has_table
        if not started:
            return
        sections.append(
            NormSection(
                doc_id=doc_id,
                anchor=open_anchor,
                ordinal=len(sections),
                breadcrumb=open_breadcrumb,
                heading=open_heading,
                body="\n\n".join(open_parts),
                has_table=open_has_table,
            )
        )
        started, open_anchor, open_heading = False, None, None
        open_parts, open_has_table = [], False

    for item in parsed.sections:
        is_heading = bool(_HEADING_RE.match(item.tag))
        if is_heading:  # breadcrumb updates BEFORE the section opens, so a chapter heading
            if _CHAPTER_RE.match(item.text):  # carries its own chapter in the crumb
                chapter, paragraph = item.text, None
            elif _PARAGRAPH_RE.match(item.text):
                paragraph = item.text
        # a new section starts on every z-anchor (the source puts them on clauses) and on
        # every heading, so a heading never gets buried inside the previous clause's body
        if not started or item.anchor is not None or is_heading:
            flush()
            started = True
            open_anchor = item.anchor
            open_breadcrumb = " › ".join(c for c in (chapter, paragraph) if c)
        if is_heading and open_heading is None and not open_parts:
            open_heading = item.text
        else:
            open_parts.append(item.text)
        open_has_table = open_has_table or item.tag == "table"
    flush()

    url = source_url or f"https://adilet.zan.kz/rus/docs/{doc_id}"
    digest = hashlib.sha256(unicodedata.normalize("NFC", parsed.markdown).encode("utf-8"))
    meta: dict[str, str] = {}
    if parsed.freshness:
        meta["freshness"] = parsed.freshness
    if parsed.subtitle:
        meta["registration"] = parsed.subtitle

    document = NormDocument(
        id=doc_id,
        title=parsed.title,
        subtitle=parsed.subtitle,
        freshness=parsed.freshness,
        status=status,
        status_note=status_note,
        status_conflict=conflict,
        source_url=url,
        content_sha256=digest.hexdigest(),
        meta=meta,
    )
    links = _grab_edges(body, "a[href*='/rus/docs/']") if body is not None else []
    return ParsedNorm(document=document, sections=sections, links=links)
