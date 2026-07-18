"""Bill of quantities / спецификация (docs/13).

Groups identical protective devices (class + In + curve + RCD) counted in штуках, and identical
cables (cores + section + material + insulation + method) summed in метрах (with the journal's
reserve margin). Deterministic ordering so the output is stable/testable.
"""
from __future__ import annotations

from typing import Any

from .cable_journal import cable_margin
from .tables import Table, fmt_num as _fmt

_COLUMNS = ["№", "Категория", "Наименование", "Ед. изм.", "Кол-во"]


def _device_name(spec: dict[str, Any]) -> str:
    curve = f" {spec['curve']}" if spec.get("curve") else ""
    poles = "1P" if spec.get("phases") == 1 else "3P"  # 1P vs 3P/4P are different products
    rcd = spec.get("rcd", {}) or {}
    rcd_s = f" + УЗО {_fmt(rcd.get('ma') or 30)}мА" if rcd.get("present") else ""
    return f"{spec.get('device_class', '')} {_fmt(spec.get('In_a', ''))}A {poles}{curve}{rcd_s}"


def _cable_name(spec: dict[str, Any]) -> str:
    return (f"Кабель {spec.get('cores', '')} {_fmt(spec.get('section_mm2', ''))} мм² "
            f"{spec.get('material', '')}/{spec.get('insulation', '')} · метод {spec.get('method', '')}")


def build_boq(project: dict[str, Any], report: dict[str, Any]) -> Table:
    margin = cable_margin(project)
    devices: dict[str, int] = {}
    cables: dict[str, float] = {}
    for r in report.get("rows", []) or []:
        spec = r.get("spec", {}) or {}
        dname = _device_name(spec)
        devices[dname] = devices.get(dname, 0) + 1
        cname = _cable_name(spec)
        # round each row's with-margin length (matching cable_journal) BEFORE summing, so the
        # BoQ total reconciles with the sum of the journal's per-row lengths.
        cables[cname] = cables.get(cname, 0.0) + round(float(r.get("length_m", 0) or 0) * margin, 1)

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
        report.get("data_identity", ""),
        report.get("signoff_notice", ""),
        report.get("provenance_note", ""),
        report.get("disclaimer", ""),
    ]
    return Table(title="Спецификация", columns=_COLUMNS, rows=rows,
                 notes=[n for n in notes if n])
