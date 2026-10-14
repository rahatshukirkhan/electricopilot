"""Single-line diagram layout (docs/13): a distribution board report → one Drawing per sheet.

A3 landscape (420×297 mm). Input on the left feeds a horizontal bus; outgoing circuits hang
as evenly spaced vertical columns. Per circuit, top→down: breaker, RCD (if any), a rotated
summary label (device/cable/length/IB/ΔU), the description and status. Wire colour encodes
status. More than ~16 circuits split across sheets. Layout lives here; svg.py/dxf.py just render.
"""
from __future__ import annotations

from typing import Any

from ..project_contract import ProjectInput, project_payload
from .primitives import Drawing, Line, Text
from .svg import render_svg
from .tables import fmt_num as _fmt
from . import symbols

SHEET_W, SHEET_H = 420.0, 297.0
MARGIN = 12.0
BUS_Y = 44.0
BUS_X0, BUS_X1 = 26.0, SHEET_W - MARGIN - 4
BREAKER_Y = 60.0
RCD_Y = 76.0
ARROW_Y = 218.0          # long drop fills the A3 sheet and clears the rotated summary label
MAX_PER_SHEET = 16

# design-v2-spec §3: status colours as the ok/warn/bad tokens (styles.css :root); burgundy is
# reserved for brand accents, not used for status here. NEEDS_REVIEW darkened from #A9701E to
# #8A5A16 alongside the CSS --warn token (contrast review: the old amber was ~4.18:1 on white,
# below the spec's own ≥4.5:1 bar) — kept identical to styles.css so the SVG and the app UI never
# drift onto two different "warn" colours.
STATUS_COLOR = {"PASS": "#2E7D4F", "FAIL": "#C8321F", "NEEDS_REVIEW": "#8A5A16"}
_STATUS_LABEL = {"PASS": "PASS", "FAIL": "FAIL", "NEEDS_REVIEW": "REVIEW"}

# design-v2-spec §3 "заодно": report["data_identity"] (calculation_manifest.py) is built for
# cross-surface consistency — tests/test_export.py asserts the SAME raw codes ("illustrative",
# "NEEDS_REVIEW", …) appear verbatim in every document surface (SVG/DXF/XLSX/markdown), so this
# helper GLOSSES rather than replaces: the machine-readable code stays intact (audit trail,
# cross-surface grep), a plain-Russian reading is appended right next to it — only for the
# diagram caption drawn here, not for the shared string other surfaces/consumers rely on as-is.
_PROVENANCE_GLOSS = {
    "illustrative": "синтетические (демо)",
    "NEEDS_REVIEW": "требует проверки",
}


def _gloss_provenance(text: str) -> str:
    for code, ru in _PROVENANCE_GLOSS.items():
        text = text.replace(code, f"{code} — {ru}")
    return text


def _device_label(spec: dict[str, Any]) -> str:
    curve = f" {spec['curve']}" if spec.get("curve") else ""
    return f"{spec['device_class']} {_fmt(spec['In_a'])}A{curve}"


def _cable_label(spec: dict[str, Any]) -> str:
    return (f"{spec['cores']} {_fmt(spec['section_mm2'])} мм² "
            f"{spec['material']}/{spec['insulation']} · {spec['method']}")


def _input_feed(project: dict[str, Any]) -> list[Any]:
    supply = project.get("supply", {}) or {}
    prims: list[Any] = [
        Line(BUS_X0, 28.0, BUS_X0, BUS_Y, layer="BUS", width=1.2),
        symbols.load_arrow(BUS_X0, 24.0)[0],  # incomer marker (reused triangle, pointing down)
        Text(BUS_X0 - 2, 22.0, "Ввод", height=2.6, anchor="end"),
        Text(BUS_X0 + 3, 34.0,
             f"{_fmt(supply.get('voltage_v', 400))} В · {_fmt(supply.get('phases', 3))}ф · "
             f"{supply.get('earthing', 'TN-C-S')}", height=2.2, anchor="start"),
    ]
    return prims


def _draw_circuit(d: Drawing, x: float, row: dict[str, Any]) -> None:
    spec = row.get("spec", {}) or {}
    status = row.get("status", "")
    color = STATUS_COLOR.get(status, "#8B93A5")  # --faint fallback for an unknown/missing status

    d.add(Line(x, BUS_Y, x, ARROW_Y, layer="WIRES", color=color, width=0.4))
    # gG fuse gets the fuse symbol; MCB/MCCB the switch symbol
    device_sym = symbols.fuse if spec.get("device_class") == "gG_fuse" else symbols.breaker
    d.extend(device_sym(x, BREAKER_Y))
    if spec.get("rcd", {}).get("present"):
        d.extend(symbols.rcd(x, RCD_Y))
    d.extend(symbols.load_arrow(x, ARROW_Y))

    d.add(Text(x, BUS_Y - 2.5, str(row.get("ref", "")), height=2.6, anchor="middle"))

    # rotated (reads upward) summary beside the wire — compact for dense columns
    summary = (f"{_device_label(spec)}"
               f"{'  УЗО ' + _fmt(spec['rcd'].get('ma') or 30) + 'мА' if spec.get('rcd', {}).get('present') else ''}"
               f"  ·  {_cable_label(spec)}"
               f"  ·  L={_fmt(row.get('length_m', ''))}м"
               f"  ·  IB={_fmt(row.get('IB_a', ''))}A ΔU={_fmt(row.get('dU_pct', ''))}%")
    d.add(Text(x + 4.0, ARROW_Y - 6.0, summary, height=1.9, anchor="start", rotation=90))

    desc = str(row.get("description", "") or "")
    if desc:
        d.add(Text(x, ARROW_Y + 8.0, desc[:22], height=2.2, anchor="middle"))
    d.add(Text(x, ARROW_Y + 12.5, _STATUS_LABEL.get(status, status),
               height=2.2, anchor="middle", color=color))


def build_sld(project: ProjectInput, report: dict[str, Any]) -> list[Drawing]:
    """One Drawing per sheet. Empty board → a single sheet with just the frame."""
    data = project_payload(project)
    rows = report.get("rows", []) or []
    chunks = [rows[i:i + MAX_PER_SHEET] for i in range(0, len(rows), MAX_PER_SHEET)] or [[]]
    name = str(data["name"])
    board_ref = str(data["board_ref"])

    sheets: list[Drawing] = []
    for si, chunk in enumerate(chunks):
        d = Drawing(SHEET_W, SHEET_H)
        d.extend(symbols.sheet_frame(
            SHEET_W, SHEET_H,
            title=f"{name} · {board_ref}".strip(" ·"),
            subtitle=f"Однолинейная схема · лист {si + 1}/{len(chunks)}"))
        d.extend(symbols.bus(BUS_X0, BUS_X1, BUS_Y))
        d.extend(_input_feed(data))

        n = len(chunk)
        col_span = BUS_X1 - (BUS_X0 + 22.0)
        for i, row in enumerate(chunk):
            col_x = BUS_X0 + 22.0 + col_span * (i + 0.5) / max(n, 1)
            _draw_circuit(d, col_x, row)
        _draw_footer(d, report)
        sheets.append(d)
    return sheets


def _draw_footer(d: Drawing, report: dict[str, Any]) -> None:
    """Advisory disclaimer + data-pack provenance on every sheet (docs/13 §each doc carries it)."""
    d.add(Text(MARGIN + 2, SHEET_H - MARGIN - 3.5,
               str(report.get("disclaimer", "")), height=1.65, anchor="start", color="#8A5A16"))  # --warn
    d.add(Text(MARGIN + 2, SHEET_H - MARGIN - 1.0,
               str(report.get("signoff_notice", "UNSIGNED_ADVISORY")),
               height=1.65, anchor="start", color="#8A5A16"))  # --warn
    d.add(Text(MARGIN + 190, SHEET_H - MARGIN - 1.0,
               _gloss_provenance(str(report.get("data_identity", ""))),
               height=1.65, anchor="start", color="#5A6478"))  # --dim


def sld_sheets_svg(project: ProjectInput, report: dict[str, Any]) -> list[str]:
    return [render_svg(d) for d in build_sld(project, report)]
