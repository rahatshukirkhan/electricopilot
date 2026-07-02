"""Cable journal (кабельный журнал) after the ГОСТ 21.613 form (docs/13).

Columns: Обозначение | Начало | Конец | Марка (заглушка) | Сечение | Длина по проекту, м |
Длина с запасом, м | Способ прокладки. The cable MARK is a stub — real product marks are
catalogue data (Phase 6), so it is honestly labelled, not invented. The reserve coefficient
is project.export_settings.cable_margin (default 1.05).
"""
from __future__ import annotations

from typing import Any

from .tables import Table

DEFAULT_CABLE_MARGIN = 1.05
_COLUMNS = [
    "Обозначение", "Начало", "Конец", "Марка (заглушка)", "Сечение",
    "Длина по проекту, м", "Длина с запасом, м", "Способ прокладки",
]


def _margin(project: dict[str, Any]) -> float:
    settings = project.get("export_settings") or {}
    try:
        m = float(settings.get("cable_margin", DEFAULT_CABLE_MARGIN))
    except (TypeError, ValueError):
        m = DEFAULT_CABLE_MARGIN
    return m if m >= 1.0 else DEFAULT_CABLE_MARGIN


def _fmt(v: Any) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def build_cable_journal(project: dict[str, Any], report: dict[str, Any]) -> Table:
    margin = _margin(project)
    start = f"Щит {project.get('board_ref', '') or project.get('name', '')}".strip()
    rows: list[list[Any]] = []
    for r in report.get("rows", []) or []:
        spec = r.get("spec", {}) or {}
        length = r.get("length_m", 0) or 0
        section = f"{spec.get('cores', '')} {_fmt(spec.get('section_mm2', ''))} мм²".strip()
        mark = f"(марка по проекту, {spec.get('material', '')}/{spec.get('insulation', '')})"
        rows.append([
            r.get("ref", ""), start, r.get("description", "") or "—",
            mark, section, _fmt(length), _fmt(round(float(length) * margin, 1)),
            spec.get("method", ""),
        ])
    notes = [
        f"Запас длины: коэффициент {margin:g} (project.export_settings.cable_margin).",
        "«Марка» — заглушка: конкретная марка кабеля назначается по проекту/каталогу.",
        report.get("provenance_note", ""),
        report.get("disclaimer", ""),
    ]
    return Table(title="Кабельный журнал", columns=_COLUMNS, rows=rows,
                 notes=[n for n in notes if n])
