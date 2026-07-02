#!/usr/bin/env python3
"""Extract a `pue-rk` norm pack from the official ПУЭ РК text (docs/12 §1.2, docs/04 §4.6).

Source: https://adilet.zan.kz/rus/docs/V1500010851 (Правила устройства электроустановок,
приказ Министра энергетики РК от 20.03.2015 №230, действующие). Every numeric value in the
resulting pack is parsed FROM THE DOWNLOADED HTML by code below — nothing is typed from memory
(docs/12 §0.2). Tables used (Приложение 1 «Технические данные»):

  Табл. 3  — поправочные коэффициенты на ток по температуре среды (ambient_correction)
  Табл. 4  — допустимый длительный ток, провода Cu, резина/ПВХ изоляция (ampacity, Cu/PVC)
  Табл. 5  — то же для Al (ampacity, Al/PVC)
  Табл. 48 — коэффициент k проводника, входящего в кабель (k_material, адиабатика)
  п. 40 текста Правил — снижающие коэффициенты 0,68/0,63/0,6 для 5-6/7-9/10-12 проводов
             в трубе/коробе/пучке (grouping_correction; это не отдельная таблица — цитата
             в _citation даётся как "п. 40 Правил", без номера таблицы)

Scope (v1, confirmed with the customer 2026-07-02): install methods C (открыто/воздух) и B1
(в трубе), Cu+Al, PVC-изоляция only — табл. 4/5 не дают СПЭ(XLPE) для проводов; методы/
изоляции без покрытия в источнике в паке просто отсутствуют (honest off-table, docs/03 §3.3).
vd_limits_pct НЕ найден в Прил.1 (вне области действия его таблиц) — сохранён как в iec-stub,
отдельно помечен в _citation.note как требующий отдельного источника (напр. СН РК 4.04-07-2019).

Usage:
    uv run python scripts/extract_pue_rk.py [--cache FILE.html] [--out FILE.json]

--cache reads/writes a local copy of the fetched HTML (dev convenience, not committed) so
repeated runs while iterating on the parser don't re-download ~3.6 MB each time.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from electricopilot.data.loader import pack_key

SOURCE_URL = "https://adilet.zan.kz/rus/docs/V1500010851"
SOURCE_DOCUMENT = (
    "ПУЭ РК (Правила устройства электроустановок), приказ Министра энергетики РК "
    "от 20.03.2015 №230, Приложение 1 «Технические данные»"
)

CELL_RE = re.compile(r"<t([dh])\b([^>]*)>(.*?)</t\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- fetch

def fetch_html(cache: Path | None) -> str:
    if cache is not None and cache.is_file():
        return cache.read_text(encoding="utf-8")
    try:
        resp = httpx.get(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"},
                         timeout=60, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text
    except httpx.ConnectError:
        # adilet.zan.kz serves an incomplete TLS chain (missing intermediate) that OpenSSL's
        # root-only bundle can't complete, so httpx fails where curl (system trust store,
        # builds the chain) succeeds. Fall back to curl — it STILL verifies the certificate
        # (no -k), which matters for a norm source (a MITM must not swap in wrong values).
        html = _fetch_via_curl()
    if cache is not None:
        cache.write_text(html, encoding="utf-8")
    return html


def _fetch_via_curl() -> str:
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError(
            "httpx could not verify the source's TLS chain and curl is not available. "
            "Download the page manually (browser/curl) and pass it via --cache FILE.html."
        )
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell, verified cert (no -k)
        [curl, "-fsSL", "-A", "Mozilla/5.0", SOURCE_URL],
        capture_output=True, timeout=120, check=True,
    )
    return out.stdout.decode("utf-8")


def _flat_text(html: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", html)).strip()


def find_table_html(html: str, table_no: int) -> str:
    marker = f"Таблица {table_no}."
    pos = html.find(marker)
    if pos < 0:
        raise ValueError(f"marker '{marker}' not found in source HTML")
    start = html.find("<table", pos)
    if start < 0:
        raise ValueError(f"no <table> found after '{marker}'")
    end = html.find("</table>", start)
    if end < 0:
        raise ValueError(f"unterminated <table> after '{marker}'")
    return html[start : end + len("</table>")]


# --------------------------------------------------------------------------- grid parser

def _attr_int(attrs: str, name: str, default: int = 1) -> int:
    m = re.search(name + r'\s*=\s*"?(\d+)"?', attrs, re.IGNORECASE)
    return int(m.group(1)) if m else default


def _cell_lines(inner: str) -> list[str]:
    parts = re.split(r"<br\s*/?>", inner, flags=re.IGNORECASE)
    lines = []
    for p in parts:
        t = TAG_RE.sub("", p).replace("&nbsp;", " ")
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            lines.append(t)
    return lines


def parse_table_grid(table_html: str) -> list[list[list[str]]]:
    """grid[row][col] = list[str] cell text lines (a ПУЭ table packs a run of values into
    one <td> via <br>; grid preserves that as multiple lines per cell). Handles rowspan/
    colspan by carrying a spanning cell's lines into the covered rows/cols."""
    rows_html = re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_html, re.IGNORECASE | re.DOTALL)
    grid: list[list[list[str]]] = []
    pending: dict[int, tuple[int, list[str]]] = {}  # col -> (rows_remaining_after_this_row, lines)
    for row_html in rows_html:
        row: dict[int, list[str]] = {}
        carried_cols = set(pending.keys())
        for c in carried_cols:
            row[c] = pending[c][1]
        cells = CELL_RE.findall(row_html)
        col = 0
        new_spans: dict[int, tuple[int, list[str]]] = {}
        for _tag, attrs, inner in cells:
            while col in row:
                col += 1
            rs = _attr_int(attrs, "rowspan")
            cs = _attr_int(attrs, "colspan")
            lines = _cell_lines(inner)
            for _ in range(cs):
                while col in row:
                    col += 1
                row[col] = lines
                if rs > 1:
                    new_spans[col] = (rs - 1, lines)
                col += 1
        width = max(row.keys(), default=-1) + 1
        grid.append([row.get(c, []) for c in range(width)])
        next_pending: dict[int, tuple[int, list[str]]] = {}
        for c in carried_cols:
            remaining, lines = pending[c]
            remaining -= 1
            if remaining > 0:
                next_pending[c] = (remaining, lines)
        next_pending.update(new_spans)
        pending = next_pending
    return grid


def _num(s: str) -> float:
    return float(s.replace(",", ".").replace("–", "-").strip())


# --------------------------------------------------------------------------- table 4 / 5 (ampacity)

def extract_ampacity_table(html: str, table_no: int) -> dict:
    """Табл. 4 (Cu) / 5 (Al): секции + открыто + «одного двух/трёхжильного в трубе»."""
    grid = parse_table_grid(find_table_html(html, table_no))
    header_flat = " ".join(" ".join(c) for row in grid[:3] for c in row)
    header_norm = header_flat.replace("-", "").replace(" ", "")
    for needed in ("Сечение", "открыто", "двухжил", "трехжил"):
        if needed not in header_flat and needed.replace(" ", "") not in header_norm:
            raise ValueError(f"Табл.{table_no}: header sanity check failed, missing '{needed}'")
    # data rows: section column packs >=2 values via <br> (header/footnote rows don't)
    data_rows = [r for r in grid if r and r[0] and len(r[0]) >= 2]
    if not data_rows:
        raise ValueError(f"Табл.{table_no}: no packed data rows found")
    sections: list[float] = []
    open_it: list[float] = []
    tube2_it: list[float] = []
    tube3_it: list[float] = []
    for row in data_rows:
        n = len(row[0])
        sec = [_num(x) for x in row[0]]
        op = [row[1][i] if i < len(row[1]) else "–" for i in range(n)]
        t2 = [row[5][i] if i < len(row[5]) else "–" for i in range(n)]
        t3 = [row[6][i] if i < len(row[6]) else "–" for i in range(n)]
        for s, o, a, b in zip(sec, op, t2, t3):
            if s < 1.5:
                continue  # legacy sub-1.5mm2 cord gauges, outside our standard_sections_mm2 series
            sections.append(s)
            open_it.append(_num(o) if o not in ("–", "-", "") else None)
            tube2_it.append(_num(a) if a not in ("–", "-", "") else None)
            tube3_it.append(_num(b) if b not in ("–", "-", "") else None)
    return {"sections": sections, "open": open_it, "tube_n2": tube2_it, "tube_n3": tube3_it}


# --------------------------------------------------------------------------- table 3 (ambient)

def extract_ambient_correction(html: str) -> dict:
    """Табл. 3, строка «условная температура среды=25°C, нормированная температура жил=65°C»
    — резолвится п.40 текста Правил (Табл.4/5 приняты для жил+65°C, воздуха+25°C)."""
    grid = parse_table_grid(find_table_html(html, 3))
    data_row = grid[-1]
    cond_temps = [_num(x) for x in data_row[0]]
    normed_temps = [_num(x) for x in data_row[1]]
    idx = next(
        (i for i, (c, n) in enumerate(zip(cond_temps, normed_temps)) if c == 25 and n == 65),
        None,
    )
    if idx is None:
        raise ValueError("Табл.3: no row with условная=25 / нормированная=65 found")
    col_temps_flat = " ".join(" ".join(c) for c in grid[1])
    if "+ 25" not in col_temps_flat and "25" not in col_temps_flat:
        raise ValueError("Табл.3: header sanity check failed (temperature columns)")
    temp_labels = ["-5", "0", "5", "10", "15", "20", "25", "30", "35", "40", "45", "50"]
    factors = [_num(col[idx]) for col in data_row[2:]]
    if len(factors) != len(temp_labels):
        raise ValueError(f"Табл.3: expected {len(temp_labels)} temperature columns, got {len(factors)}")
    base_idx = temp_labels.index("25")
    if abs(factors[base_idx] - 1.0) > 1e-9:
        raise ValueError(
            f"Табл.3: sanity check failed — factor at base temp +25°C should be 1.00, got {factors[base_idx]}"
        )
    return dict(zip(temp_labels, factors))


# --------------------------------------------------------------------------- table 48 (k adiabatic)

def extract_k_adiabatic(html: str) -> dict:
    grid = parse_table_grid(find_table_html(html, 48))
    header_flat = " ".join(" ".join(c) for row in grid[:2] for c in row)
    if "Поливинилхлорид" not in header_flat or "Сшитый полиэтилен" not in header_flat:
        raise ValueError("Табл.48: header sanity check failed (insulation columns)")
    k_row = next((r for r in grid if r and r[0] and "медного" in r[0][0]), None)
    if k_row is None:
        raise ValueError("Табл.48: k-value row ('медного|алюминиевого') not found")
    pvc_cu, pvc_al = _num(k_row[1][0]), _num(k_row[1][1])
    xlpe_cu, xlpe_al = _num(k_row[2][0]), _num(k_row[2][1])
    return {"Cu/PVC": pvc_cu, "Al/PVC": pvc_al, "Cu/XLPE": xlpe_cu, "Al/XLPE": xlpe_al}


# --------------------------------------------------------------------------- п.40 grouping text

def extract_grouping_correction(html: str) -> dict:
    text = _flat_text(html)
    idx = text.find("снижающих коэффициентов")
    if idx < 0:
        raise ValueError("п.40: 'снижающих коэффициентов' phrase not found")
    window = text[idx : idx + 200]
    # non-greedy .{0,N}? — a greedy quantifier here can match on a stray digit inside the
    # PREVIOUS number (e.g. the '3' in '0,63') before reaching the real next coefficient.
    m = re.search(
        r"(\d[,.]?\d*)\s*для\s*5\s*и\s*6.{0,10}?(\d[,.]?\d*)\s*для\s*7.{0,3}?9"
        r".{0,10}?(\d[,.]?\d*)\s*для\s*10.{0,3}?12",
        window,
    )
    if not m:
        raise ValueError(f"п.40: could not parse grouping coefficients from: {window!r}")
    f56, f79, f1012 = (_num(x) for x in m.groups())
    out = {str(n): 1.0 for n in range(1, 5)}
    out.update({"5": f56, "6": f56, "7": f79, "8": f79, "9": f79, "10": f1012, "11": f1012, "12": f1012})
    return out


# --------------------------------------------------------------------------- pack assembly

def build_pack(html: str) -> dict:
    cu = extract_ampacity_table(html, 4)
    al = extract_ampacity_table(html, 5)
    ambient = extract_ambient_correction(html)
    grouping = extract_grouping_correction(html)
    k = extract_k_adiabatic(html)

    def _method_c_b1(material_data: dict) -> dict:
        keys = [pack_key(s) for s in material_data["sections"]]
        by_section_open = dict(zip(keys, material_data["open"]))
        by_section_n2 = dict(zip(keys, material_data["tube_n2"]))
        by_section_n3 = dict(zip(keys, material_data["tube_n3"]))
        # method C ("открыто"): source gives one value regardless of conductor count (docs/04 §4.6)
        c_table = {k_: v for k_, v in by_section_open.items() if v is not None}
        n2_table = {k_: v for k_, v in by_section_n2.items() if v is not None}
        n3_table = {k_: v for k_, v in by_section_n3.items() if v is not None}
        return c_table, n2_table, n3_table

    cu_c, cu_b1_n2, cu_b1_n3 = _method_c_b1(cu)
    al_c, al_b1_n2, al_b1_n3 = _method_c_b1(al)

    cite_amp = {
        "standard": "ПУЭ РК", "table": "4 (Cu), 5 (Al)",
        "note": (
            "Cu: Табл.4, Al: Табл.5. 'открыто' не различает число жил (источник даёт одно "
            "значение вне зависимости от количества проводов) — метод C хранит одно и то же "
            "It для n=2 и n=3. Метод B1 использует колонки «одного двух/трёхжильного в трубе». "
            "Условия по п.40: жилы +65°C, воздух +25°C, земля +15°C."
        ),
        "source_document": SOURCE_DOCUMENT, "source_url": SOURCE_URL,
        "entered_by": "", "verified_by": "",
    }
    ampacity = {
        "C": {
            "Cu": {"PVC": {"2": cu_c, "3": cu_c}},
            "Al": {"PVC": {"2": al_c, "3": al_c}},
        },
        "B1": {
            "Cu": {"PVC": {"2": cu_b1_n2, "3": cu_b1_n3}},
            "Al": {"PVC": {"2": al_b1_n2, "3": al_b1_n3}},
        },
        "_citation": cite_amp,
    }

    ambient_correction = {
        "PVC": ambient,
        "_citation": {
            "standard": "ПУЭ РК", "table": "3",
            "note": "Строка: условная температура среды +25°C, нормированная температура жил "
                    "+65°C (соответствует условиям Табл.4/5 по п.40 текста Правил).",
            "source_document": SOURCE_DOCUMENT, "source_url": SOURCE_URL,
            "entered_by": "", "verified_by": "",
        },
    }
    grouping_correction = dict(grouping)
    grouping_correction["_citation"] = {
        "standard": "ПУЭ РК", "table": None,
        "note": "п. 40 текста Правил (не отдельная таблица): снижающие коэффициенты 0,68/0,63/0,6 "
                "для 5-6/7-9/10-12 одновременно нагруженных проводов в трубе/коробе/пучке; "
                "1-4 провода — без снижения (built into which Табл.4/5 column is selected).",
        "source_document": SOURCE_DOCUMENT, "source_url": SOURCE_URL,
        "entered_by": "", "verified_by": "",
    }
    k_material = dict(k)
    k_material["_citation"] = {
        "standard": "ПУЭ РК", "table": "48",
        "note": "k для проводника, входящего в (много)жильный кабель — используется как k "
                "адиабатики фазного проводника (аналог IEC 60364-4-43 Табл.43A).",
        "source_document": SOURCE_DOCUMENT, "source_url": SOURCE_URL,
        "entered_by": "", "verified_by": "",
    }

    # Not sourced from Прил.1 (out of scope of its tables) — kept as in iec-stub, honestly flagged.
    vd_limits_pct = {"lighting": 3, "power": 5, "socket": 5, "motor": 5, "general": 5}

    # Universal engineering constants (not IEC/ПУЭ-table copyrighted expression), same as iec-stub.
    device_classes = {
        "MCB": {"i2_over_in": 1.45, "citation": {"standard": "IEC 60898", "note": "conventional tripping I2=1.45*In"}},
        "MCCB": {"i2_over_in": 1.30, "citation": {"standard": "IEC 60947-2"}},
        "gG_fuse": {"i2_over_in": 1.60, "citation": {"standard": "IEC 60269"}},
    }

    pack = {
        "meta": {
            "name": "pue-rk", "version": "0.1.0", "status": "public_standard",
            "source_note": (
                "Значения ампакитности/поправок/адиабатики извлечены кодом из официального "
                "текста ПУЭ РК (Табл. 3, 4, 5, 48 Прил.1; п.40). vd_limits_pct НЕ найден в "
                "Прил.1 и сохранён как в iec-stub (см. _citation.note того раздела) — "
                "требует отдельного источника для повышения статуса."
            ),
            "source_document": SOURCE_DOCUMENT,
        },
        "standard_ratings_a": [6, 10, 13, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400],
        "standard_sections_mm2": [1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300],
        "device_classes": device_classes,
        "k_material": k_material,
        "resistivity_ohm_mm2_per_m": {"Cu": 0.0225, "Al": 0.036},
        "reactance_ohm_per_m": 0.00008,
        "vd_limits_pct": vd_limits_pct,
        "reference_ambient_c": {"air": 25, "ground": 15},
        "ambient_correction": ambient_correction,
        "grouping_correction": grouping_correction,
        "ampacity": ampacity,
        "citations": {
            "IB_load": {"standard": "IEC 60364-5-52", "clause": "523", "note": "методология не меняется (движок IEC)"},
            "In_selection": {"standard": "IEC 60364-4-43", "clause": "433.1"},
            "coord_overload": {"standard": "IEC 60364-4-43", "clause": "433.1"},
            "voltage_drop": {"standard": "IEC 60364-5-52", "clause": "Annex G (informative)",
                              "note": "vd_limits_pct не из ПУЭ РК Прил.1, см. meta.source_note"},
            "sc_adiabatic": {"standard": "ПУЭ РК", "table": "48"},
            "install_method": {"standard": "ПУЭ РК", "table": "4, 5",
                                "note": "открыто->C, в трубе (одного N-жильного)->B1"},
        },
    }
    return pack


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=None, help="local HTML cache path (dev only)")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[1] / "src/electricopilot/data/packs/pue-rk.json")
    args = ap.parse_args()

    html = fetch_html(args.cache)
    pack = build_pack(html)
    args.out.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")
    print("Run `uv run python scripts/pack_review.py pue-rk` next — MANDATORY human review before commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
