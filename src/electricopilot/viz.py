"""Deterministic visualization data for ElectriCopilot Studio (docs/10).

Every figure is derived from the audited SizingResult and carries norm citations. Numbers
come from the engine / synthetic pack — never invented. Time-current (TCC) curve SHAPES follow
device-standard methodology; the curve PARAMETERS are synthetic/illustrative (see pack.trip_curves).
"""
from __future__ import annotations

import math
from typing import Any

from .data.loader import DataPack, load_data_pack
from .engine import size
from .engine.ampacity import corrected_ampacity
from .engine.voltage_drop import voltage_drop
from .exceptions import DataPackError
from .models import Citation, InstallationConditions, SizingRequest, SizingResult

_TMAX = 10000.0   # s, plot ceiling
_TMIN = 1e-3      # s, plot floor


def _resolve(req: SizingRequest) -> InstallationConditions:
    cond = req.installation
    if cond.loaded_conductors is None:
        cond = cond.model_copy(update={"loaded_conductors": 2 if req.load.phases == 1 else 3})
    return cond


def _logspace(lo: float, hi: float, n: int) -> list[float]:
    lo = max(lo, 1e-6)
    a, b = math.log10(lo), math.log10(hi)
    return [10 ** (a + (b - a) * i / (n - 1)) for i in range(n)]


def _req_iz(result: SizingResult) -> float:
    p = result.selected_protection
    return max(p.In_a, p.I2_a / 1.45)


def sweep(req: SizingRequest, result: SizingResult, pack: DataPack) -> dict[str, Any]:
    cond = _resolve(req)
    ib = result.design_current_a
    req_iz = _req_iz(result)
    vd_limit = result.voltage_drop_limit_pct
    s_min = result.adiabatic_min_mm2
    rows = []
    n_cond = int(cond.loaded_conductors or (2 if req.load.phases == 1 else 3))
    for s in pack.sections():
        # Only sections this pack covers for the combination are real candidates; a partially
        # populated pack (e.g. pue-rk B1/Al) would otherwise raise mid-sweep (docs/04 §4.6).
        if not pack.has_ampacity(cond.method, cond.material, cond.insulation, n_cond, s):
            continue
        it, _pk, iz, _ = corrected_ampacity(s, cond, pack)
        _dv, du_pct, _ = voltage_drop(req.load, cond, s, ib, pack)
        rows.append({
            "section_mm2": s, "It_a": round(it, 2), "Iz_a": round(iz, 2),
            "vd_pct": round(du_pct, 3),
            "amp_ok": iz >= req_iz - 1e-9,
            "vd_ok": du_pct <= vd_limit + 1e-9,
            "sc_ok": s_min is None or s >= s_min - 1e-9,
        })
    return {
        "rows": rows,
        "required_iz_a": round(req_iz, 2),
        "chosen_mm2": result.selected_cable.cross_section_mm2,
        "governing": result.selected_cable.governing_constraint,
        "citation": _c(pack.citation("coord_overload")),
    }


def vd_profile(req: SizingRequest, result: SizingResult, pack: DataPack) -> dict[str, Any]:
    cond = _resolve(req)
    ib = result.design_current_a
    s = result.selected_cable.cross_section_mm2
    lengths = [1 + (2 * cond.length_m - 1) * i / 39 for i in range(40)]
    series = []
    for L in lengths:
        c = cond.model_copy(update={"length_m": L})
        _dv, pct, _ = voltage_drop(req.load, c, s, ib, pack)
        series.append([round(L, 2), round(pct, 3)])
    return {
        "section_mm2": s, "series": series,
        "limit_pct": result.voltage_drop_limit_pct,
        "current_length_m": cond.length_m,
        "current_pct": result.voltage_drop_pct,
        "citation": _c(pack.citation("voltage_drop")),
    }


def derating(req: SizingRequest, result: SizingResult, pack: DataPack) -> dict[str, Any]:
    cond = _resolve(req)
    it, _pk, iz, _ = corrected_ampacity(result.selected_cable.cross_section_mm2, cond, pack)
    ka, ka_c = pack.ambient_factor(cond.insulation, cond.ambient_temp_c)
    kg, _ = pack.grouping_factor(cond.grouping_circuits)
    return {
        "stages": [
            {"label": "It (табл.)", "value": round(it, 2)},
            {"label": f"× ka={ka:g}", "value": round(it * ka, 2)},
            {"label": f"× kg={kg:g}  = IZ", "value": round(iz, 2)},
        ],
        "It_a": round(it, 2), "ka": ka, "kg": kg, "Iz_a": round(iz, 2),
        "required_iz_a": round(_req_iz(result), 2),
        "citation": _c(ka_c),
    }


def _mcb_t(x: float, T: float, mag: float, inst: float) -> float | None:
    if x >= mag:
        return inst
    if x > 1.13:
        return min(T / (x * x - 1.0), _TMAX)
    return None


def tcc(req: SizingRequest, result: SizingResult, pack: DataPack) -> dict[str, Any]:
    prot = req.protection
    dev = prot.device_class
    In = result.selected_protection.In_a
    ib = result.design_current_a
    iscc = prot.prospective_fault_current_a
    S = result.selected_cable.cross_section_mm2
    k, k_c = pack.k_adiabatic(req.installation.material, req.installation.insulation)
    # Device trip curves are illustrative device-standard shapes, not norm-table data — a pack
    # focused on cable ampacity (e.g. pue-rk) may not carry them. Degrade gracefully: still
    # draw the real cable adiabatic curve, just omit the device band with a note.
    params: dict[str, Any] | None
    tc_c: Citation | None
    try:
        params, tc_c = pack.trip_curve(dev)
    except DataPackError:
        params, tc_c = None, None

    hi = (iscc if iscc else In * 25) * 1.6
    grid = _logspace(max(ib * 0.7, In * 0.6), hi, 70)

    dev_min, dev_max = [], []
    if params is not None and dev in ("MCB", "MCCB"):
        mrange = params["mag_multiple"].get(getattr(prot, "trip_curve_type", "C")) \
            or next(iter(params["mag_multiple"].values()))
        mL, mH = mrange
        Tmin, Tmax = params["thermal_time_s_min"], params["thermal_time_s_max"]
        inst = params["instant_time_s"]
        for cur in grid:
            x = cur / In
            tf = _mcb_t(x, Tmin, mL, inst)
            ts = _mcb_t(x, Tmax, mH, inst)
            if tf is not None:
                dev_min.append([round(cur, 2), round(tf, 4)])
            if ts is not None:
                dev_max.append([round(cur, 2), round(ts, 4)])
    elif params is not None:  # gG fuse
        coef, exp, inst = params["coef"], params["exp"], params["instant_time_s"]
        for cur in grid:
            x = cur / In
            if x <= 1.2:
                continue
            t = min(max(coef * x ** (-exp), inst), _TMAX)
            dev_min.append([round(cur, 2), round(t * 0.7, 4)])
            dev_max.append([round(cur, 2), round(t, 4)])

    cable = []
    for cur in grid:
        t = (k * S / cur) ** 2
        if _TMIN <= t <= _TMAX:
            cable.append([round(cur, 2), round(t, 4)])

    sc_check = next((c for c in result.checks if c.name == "short_circuit"), None)
    device_available = params is not None
    note = (
        "Кривые аппарата — иллюстративные (синтетические), форма по методологии; "
        "кривая кабеля — реальная адиабатика t=(k·S/I)²."
        if device_available else
        f"Норм-пакет «{pack.meta.name}» не содержит время-токовых кривых аппарата — "
        "показана только кривая стойкости кабеля (реальная адиабатика t=(k·S/I)²)."
    )
    citations = [_c(k_c), _c(pack.citation("sc_adiabatic"))]
    if tc_c is not None:
        citations.insert(0, _c(tc_c))
    return {
        "device": {"class": dev, "curve_type": getattr(prot, "trip_curve_type", "C"),
                   "available": device_available, "min": dev_min, "max": dev_max},
        "cable": {"section_mm2": S, "k": k, "withstand": cable},
        "markers": {"IB": round(ib, 2), "In": In, "Iscc": iscc},
        "coordinated": bool(sc_check.passed) if sc_check else None,
        "note": note,
        "citations": citations,
    }


def single_line(result: SizingResult) -> dict[str, Any]:
    cab, prot = result.selected_cable, result.selected_protection
    ld = result.request.load
    return {
        "nodes": [
            {"id": "src", "label": "Источник", "sub": f"{ld.voltage_v:g} В · {ld.phases}ф"},
            {"id": "dev", "label": prot.device_class, "sub": f"In={prot.In_a:g} A"},
            {"id": "cable", "label": f"Кабель {cab.cross_section_mm2:g} мм²",
             "sub": f"{cab.material}/{cab.insulation} · IZ={cab.Iz_a:g} A"},
            {"id": "load", "label": ld.description or "Нагрузка",
             "sub": f"IB={result.design_current_a:g} A"},
        ],
        "status": result.overall_status,
        "governing": cab.governing_constraint,
    }


def _c(c: Citation) -> dict[str, Any]:
    return c.model_dump(exclude_none=True)


def build_visuals(req: SizingRequest, *, data_pack: DataPack | None = None) -> dict[str, Any]:
    pack = data_pack or load_data_pack()
    result = size(req, data_pack=pack)
    return {
        "result": result.model_dump(),
        "sweep": sweep(req, result, pack),
        "vd_profile": vd_profile(req, result, pack),
        "derating": derating(req, result, pack),
        "tcc": tcc(req, result, pack),
        "sld": single_line(result),
        "data_provenance_note": result.data_provenance_note,
        "disclaimer": result.disclaimer,
    }
