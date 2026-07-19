"""Project (distribution board) aggregation for Studio v2 (docs/11).

Recomputes every circuit with the REAL deterministic engine (never trusts client snapshots),
then builds a panel schedule, board totals, phase balance (real arithmetic) and an illustrative
diversity estimate, plus a full Markdown project report. Circuit `meta` (phase, RCD, category)
is schedule-only and never touches the engine.
"""
from __future__ import annotations

import math
from typing import Any

from .calculation_manifest import build_calculation_manifest, manifest_data_identity
from .data.loader import DataPack, load_data_pack
from .engine import size
from .models import DISCLAIMER, SizingRequest, provenance_note_for
from .project_contract import ProjectInput, project_payload
from .topology import incomer_current_a, phase_imbalance_pct, validate_project_topology

_DEFAULT_DIVERSITY = {  # ILLUSTRATIVE (synthetic) demand factors by load category
    "lighting": 0.9, "socket": 0.5, "motor": 1.0, "power": 0.8, "general": 0.7,
}


def _kw_kva(req: SizingRequest, ib: float) -> tuple[float, float]:
    pf = req.load.power_factor
    if req.load.power_w:
        kw = req.load.power_w / 1000.0
    else:  # derive from current
        root = 1.0 if req.load.phases == 1 else math.sqrt(3)
        kw = ib * req.load.voltage_v * root * pf / 1000.0
    return kw, (kw / pf if pf else kw)


def _cable_str(res: Any, meta: dict[str, Any]) -> str:
    c = res.selected_cable
    cores = meta.get("cores") or ("1P+N" if res.request.load.phases == 1 else "3P+N")
    return f"{cores} {c.cross_section_mm2:g} мм² {c.material}/{c.insulation} метод {res.request.installation.method}"


def _device_str(res: Any) -> str:
    p = res.selected_protection
    curve = f" {res.request.protection.trip_curve_type}" if p.device_class in ("MCB", "MCCB") else ""
    return f"{p.device_class} {p.In_a:g}A{curve}"


def _rcd_str(meta: dict[str, Any]) -> str:
    rcd = meta.get("rcd") or {}
    if not rcd.get("present"):
        return "—"
    setting = rcd.get("ma", 30)
    rendered = f"{setting:g}" if isinstance(setting, (int, float)) and not isinstance(setting, bool) else str(setting)
    return f"{rcd.get('type', 'RCD')} {rendered}мА"


def build_project_report(
    project: ProjectInput,
    *,
    data_pack: DataPack | None = None,
) -> dict[str, Any]:
    data = project_payload(project)
    pack = data_pack or load_data_pack(data.get("norm_pack"))
    topology = validate_project_topology(data)
    supply = data["supply"]
    ways_total = int(supply["ways_total"])
    diversity = {**_DEFAULT_DIVERSITY, **data["diversity"]["factors"]}

    rows: list[dict[str, Any]] = []
    counts = {"PASS": 0, "FAIL": 0, "NEEDS_REVIEW": 0}
    phase_a = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    connected_kw = connected_kva = emd_kw = 0.0
    used_provenance_sections: set[str] = set()

    for c, circuit_topology in zip(
        data["circuits"], topology.circuits, strict=True,
    ):
        meta = c.get("meta", {}) or {}
        req = circuit_topology.request
        res = size(req, data_pack=pack)
        used_provenance_sections.update(a.section for a in res.data_provenance.used_sections)
        ib = res.design_current_a
        kw, kva = _kw_kva(req, ib)
        connected_kw += kw
        connected_kva += kva

        phase = circuit_topology.phase
        if req.load.phases == 3:
            for p in phase_a:
                phase_a[p] += ib
        else:
            phase_a[phase] = phase_a.get(phase, 0.0) + ib

        cat = meta.get("diversity_category") or req.load.purpose
        emd_kw += kw * float(diversity.get(cat, 1.0))
        counts[res.overall_status] = counts.get(res.overall_status, 0) + 1

        sc = next((x for x in res.checks if x.name == "short_circuit"), None)
        cab, sp = res.selected_cable, res.selected_protection
        rcd_meta = meta.get("rcd") or {}
        rows.append({
            "id": c.get("id"), "ref": c.get("ref") or "",
            "description": req.load.description or "—",
            "kw": round(kw, 2), "kva": round(kva, 2), "pf": req.load.power_factor,
            "phase": phase, "IB_a": round(ib, 1),
            "device": _device_str(res), "rcd": _rcd_str(meta),
            "cable": _cable_str(res, meta), "length_m": req.installation.length_m,
            "Iz_a": round(cab.Iz_a, 1), "dU_pct": round(res.voltage_drop_pct, 2),
            "disc": (sc.detail if sc else "—"),
            "status": res.overall_status, "governing": cab.governing_constraint,
            "signoff": (c.get("signoff") or {}).get("status", "UNSIGNED_ADVISORY"),
            # structured fields for document export (docs/13); display strings above stay for UI
            "spec": {
                "section_mm2": cab.cross_section_mm2, "material": cab.material,
                "insulation": cab.insulation, "method": req.installation.method,
                "cores": meta.get("cores") or ("1P+N" if req.load.phases == 1 else "3P+N"),
                "In_a": sp.In_a, "device_class": sp.device_class,
                "curve": (getattr(req.protection, "trip_curve_type", "C")
                          if sp.device_class in ("MCB", "MCCB") else None),
                # `or 30`: an absent/None/0 ma falls back to the standard 30 mA so exports never
                # render "УЗО NoneмА" (a bare .get('ma', 30) does NOT fire on a present-but-None key).
                "rcd": {"present": bool(rcd_meta.get("present")),
                        "type": rcd_meta.get("type"), "ma": rcd_meta.get("ma") or 30},
                "phases": req.load.phases,
            },
        })

    imbalance = phase_imbalance_pct(phase_a, topology.supply)
    emd_kva = emd_kw / 0.9 if emd_kw else 0.0  # nominal pf 0.9 for the estimate
    incomer_md_a = incomer_current_a(emd_kva, topology.supply)
    used = len(data["circuits"])
    data_provenance = (
        pack.assess_provenance(sorted(used_provenance_sections))
        if used_provenance_sections
        else pack.publication_assessment()
    )
    board_status = "FAIL" if counts["FAIL"] else ("NEEDS_REVIEW" if counts["NEEDS_REVIEW"] else "PASS")
    if board_status == "PASS" and data_provenance.verification_status == "NEEDS_REVIEW":
        board_status = "NEEDS_REVIEW"

    board = {
        "status": board_status, "rollup": counts,
        "topology": topology.supply.model_dump(),
        "totals": {
            "connected_kw": round(connected_kw, 2), "connected_kva": round(connected_kva, 2),
            "phase": {p: {"A": round(phase_a[p], 1)} for p in phase_a},
            # The project report exposes only the measured value. Threshold evaluation
            # belongs to deterministic normcheck rule R05 and its selected data pack.
            "imbalance_pct": round(imbalance, 1) if imbalance is not None else None,
            "imbalance_applicable": imbalance is not None,
            "imbalance_flag": None,
        },
        "demand": {"emd_kw": round(emd_kw, 2), "emd_kva": round(emd_kva, 2),
                   "incomer_md_a": round(incomer_md_a, 1), "provenance": "illustrative",
                   "factors": diversity},
        "ways": {"used": used, "total": ways_total, "spare": max(0, ways_total - used),
                 "spare_pct": round(max(0, ways_total - used) / ways_total * 100, 0) if ways_total else 0},
    }
    provenance_note = provenance_note_for(data_provenance)
    manifest = build_calculation_manifest(data, pack)
    data_identity = (
        f"{manifest_data_identity(manifest)} "
        f"Проверка данных: {data_provenance.verification_status}."
    )
    signoff_notice = (
        "UNSIGNED_ADVISORY — требуется проверка и подпись квалифицированного инженера."
    )
    return {
        "board": board, "rows": rows,
        "markdown": _render_markdown(
            data, board, rows, provenance_note, data_identity, signoff_notice
        ),
        "data_provenance": data_provenance.model_dump(),
        "provenance_note": provenance_note, "disclaimer": DISCLAIMER,
        "data_identity": data_identity,
        "calculation_id": manifest.calculation_id,
        "calculation_manifest": manifest.model_dump(mode="json"),
        "signoff_notice": signoff_notice,
        "norm_pack": {
            "name": pack.meta.name,
            "version": pack.meta.version,
            "status": pack.meta.status,
            "verification_status": data_provenance.verification_status,
        },
    }


def _render_markdown(project: dict[str, Any], board: dict[str, Any], rows: list[dict[str, Any]],
                     provenance_note: str, data_identity: str, signoff_notice: str) -> str:
    supply = project.get("supply", {})
    t = board["totals"]
    L: list[str] = []
    L.append(f"# ElectriCopilot — отчёт по щиту: {project.get('name', '—')} "
             f"({project.get('board_ref', '')})")
    L.append("")
    L.append(f"> {DISCLAIMER}")
    L.append("")
    L.append(f"**Статус щита:** {board['status']}  ·  цепей: {board['rollup']}")
    L.append(f"- {data_identity}")
    L.append(f"- Расположение: {project.get('location', '—')}")
    topology = board["topology"]
    L.append(f"- Питание: {topology['voltage_v']:g} В, {topology['phases']}ф, "
             f"заземление {supply.get('earthing', 'TN-C-S')}, мест {board['ways']['used']}/{board['ways']['total']}")
    L.append("")
    L.append("## Таблица щита (panel schedule)")
    L.append("| Ref | Описание | кВт | кВА | cosφ | Фаза | IB,A | Аппарат | УЗО | Кабель | L,м | IZ,A | ΔU% | Статус |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        L.append(f"| {r['ref']} | {r['description']} | {r['kw']:g} | {r['kva']:g} | {r['pf']:g} | "
                 f"{r['phase']} | {r['IB_a']:g} | {r['device']} | {r['rcd']} | {r['cable']} | "
                 f"{r['length_m']:g} | {r['Iz_a']:g} | {r['dU_pct']:g} | {r['status']} |")
    L.append("")
    L.append("## Итоги щита")
    L.append(f"- Подключённая нагрузка: **{t['connected_kw']:g} кВт / {t['connected_kva']:g} кВА**")
    ph = t["phase"]
    if t["imbalance_applicable"]:
        L.append(f"- Баланс фаз (реальный): L1={ph['L1']['A']:g} A · L2={ph['L2']['A']:g} A · "
                 f"L3={ph['L3']['A']:g} A · перекос {t['imbalance_pct']:g}% "
                 "(оценка порога — нормоконтроль R05)")
    else:
        L.append(f"- Фазный ток (реальный): L1={ph['L1']['A']:g} A · "
                 "перекос фаз неприменим к однофазному щиту (R05: not_applicable)")
    d = board["demand"]
    L.append(f"- Расчётная нагрузка (ИЛЛЮСТРАТИВНО): **{d['emd_kw']:g} кВт / {d['emd_kva']:g} кВА**, "
             f"ток ввода ≈ {d['incomer_md_a']:g} A (df синтетические)")
    L.append(f"- Резерв мест: {board['ways']['spare']} из {board['ways']['total']}")
    L.append("")
    if provenance_note:
        L.append("## Провенанс данных")
        L.append(f"> {provenance_note}")
        L.append("")
    L.append("## Подпись инженера")
    L.append(f"- Щит: **НЕ ПОДПИСАН** — {signoff_notice}")
    return "\n".join(L)
