"""Bill of quantities / спецификация (docs/13).

Groups identical protective devices (class + In + curve + RCD) counted in штуках, and identical
cables (cores + section + material + insulation + method) summed in метрах (with the journal's
reserve margin). Deterministic ordering so the output is stable/testable.
"""
from __future__ import annotations

from typing import Any

from .cable_journal import _margin
from .tables import Table

_COLUMNS = ["№", "Категория", "Наименование", "Ед. изм.", "Кол-во"]


def _fmt(v: Any) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _device_name(spec: dict[str, Any]) -> str:
    curve = f" {spec['curve']}" if spec.get("curve") else ""
    rcd = spec.get("rcd", {}) or {}
    rcd_s = f" + УЗО {_fmt(rcd.get('ma', 30))}мА" if rcd.get("present") else ""
    return f"{spec.get('device_class', '')} {_fmt(spec.get('In_a', ''))}A{curve}{rcd_s}"


def _cable_name(spec: dict[str, Any]) -> str:
    return (f"Кабель {spec.get('cores', '')} {_fmt(spec.get('section_mm2', ''))} мм² "
            f"{spec.get('material', '')}/{spec.get('insulation', '')} · метод {spec.get('method', '')}")


def build_boq(project: dict[str, Any], report: dict[str, Any]) -> Table:
    margin = _margin(project)
    devices: dict[str, int] = {}
    cables: dict[str, float] = {}
    for r in report.get("rows", []) or []:
        spec = r.get("spec", {}) or {}
        devices[_device_name(spec)] = devices.get(_device_name(spec), 0) + 1
        length = float(r.get("length_m", 0) or 0) * margin
        cables[_cable_name(spec)] = cables.get(_cable_name(spec), 0.0) + length

    rows: list[list[Any]] = []
    i = 1
    for name in sorted(devices):
        rows.append([i, "Аппарат", name, "шт", devices[name]])
        i += 1
    for name in sorted(cables):
        rows.append([i, "Кабель", name, "м", round(cables[name], 1)])
        i += 1

    notes = [
        f"Длины кабелей — с запасом (коэффициент {margin:g}).",
        "Аппараты — обобщённо (класс/номинал/кривая/УЗО); артикулы назначаются по каталогу.",
        report.get("provenance_note", ""),
        report.get("disclaimer", ""),
    ]
    return Table(title="Спецификация", columns=_COLUMNS, rows=rows,
                 notes=[n for n in notes if n])
