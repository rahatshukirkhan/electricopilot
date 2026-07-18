"""Orchestrate the deterministic sizing pipeline into a SizingResult (docs/01 §1.8)."""
from __future__ import annotations

from ..data.loader import DataPack, load_data_pack
from ..exceptions import DataPackError
from ..models import (
    DISCLAIMER,
    Check,
    Citation,
    Governing,
    InstallationConditions,
    OverallStatus,
    ReasoningStep,
    SelectedCable,
    SelectedProtection,
    SizingRequest,
    SizingResult,
    provenance_note_for,
)
from .ampacity import corrected_ampacity
from .current import design_current
from .protection import select_rating
from .shortcircuit import adiabatic_min_section
from .voltage_drop import voltage_drop

EPS = 1e-9
_PRIORITY: list[Governing] = ["overload_coordination", "short_circuit", "voltage_drop"]


def _resolve_conditions(req: SizingRequest) -> InstallationConditions:
    cond = req.installation
    if cond.loaded_conductors is None:
        n = 2 if req.load.phases == 1 else 3
        cond = cond.model_copy(update={"loaded_conductors": n})
    return cond


def size(request: SizingRequest, *, data_pack: DataPack | None = None) -> SizingResult:
    pack = data_pack or load_data_pack()
    cond = _resolve_conditions(request)
    load, prot = request.load, request.protection

    ib, step_current = design_current(load, pack)
    in_a, step_protection = select_rating(ib, prot.device_class, pack)
    i2_ratio, _ = pack.i2_over_in(prot.device_class)
    i2_a = i2_ratio * in_a
    req_iz = max(in_a, i2_a / 1.45)  # overload coordination floor (In≤IZ and I2≤1.45·IZ)

    s_min_sc, step_sc = adiabatic_min_section(prot, cond, pack)
    vd_limit = prot.max_voltage_drop_pct
    if vd_limit is None:
        vd_limit = pack.vd_limit(load.purpose)

    sections = pack.sections()
    # A real pack may cover a method only over part of the section series (e.g. pue-rk's
    # in-conduit tables stop before 300 mm²). Sweep only the covered sections: a missing
    # section simply isn't a selectable option. Ambient/grouping off-table still errors
    # hard inside corrected_ampacity — has_ampacity only gates the per-section lookup.
    n_cond = int(cond.loaded_conductors or (2 if load.phases == 1 else 3))
    covered = [s for s in sections
               if pack.has_ampacity(cond.method, cond.material, cond.insulation, n_cond, s)]
    if not covered:  # method/material/insulation entirely absent → honest off-table error
        raise DataPackError(
            f"pack '{pack.meta.name}' has no ampacity data for method={cond.method}/"
            f"{cond.material}/{cond.insulation}/{n_cond} conductors"
        )

    s_amp: float | None = None
    s_vd: float | None = None
    s_sc: float | None = None
    chosen: dict[str, object] | None = None

    for s in covered:
        it, prod_k, iz, amp_step = corrected_ampacity(s, cond, pack)
        du_v, du_pct, vd_step = voltage_drop(load, cond, s, ib, pack)
        amp_ok = iz >= req_iz - EPS
        vd_ok = du_pct <= vd_limit + EPS
        sc_ok = s_min_sc is None or s >= s_min_sc - EPS
        if amp_ok and s_amp is None:
            s_amp = s
        if vd_ok and s_vd is None:
            s_vd = s
        if sc_ok and s_sc is None:
            s_sc = s
        if amp_ok and vd_ok and sc_ok and chosen is None:
            chosen = {"s": s, "it": it, "prod_k": prod_k, "iz": iz, "du_v": du_v,
                      "du_pct": du_pct, "amp_step": amp_step, "vd_step": vd_step}

    passed = chosen is not None
    if chosen is None:  # nothing in the covered series satisfies all active checks → FAIL
        s = covered[-1]  # largest section this pack actually covers for the combination
        it, prod_k, iz, amp_step = corrected_ampacity(s, cond, pack)
        du_v, du_pct, vd_step = voltage_drop(load, cond, s, ib, pack)
        chosen = {"s": s, "it": it, "prod_k": prod_k, "iz": iz, "du_v": du_v,
                  "du_pct": du_pct, "amp_step": amp_step, "vd_step": vd_step}

    s_final = float(chosen["s"])  # type: ignore[arg-type]
    iz_final = float(chosen["iz"])  # type: ignore[arg-type]
    du_pct_final = float(chosen["du_pct"])  # type: ignore[arg-type]

    # -- checks (always all three; docs/03 §3.4 cardinality) --
    coord_cite = pack.citation("coord_overload")
    overload_ok = (ib <= in_a + EPS <= iz_final + EPS) and (i2_a <= 1.45 * iz_final + EPS)
    checks = [
        Check(
            name="overload_coordination",
            condition="IB ≤ In ≤ IZ  и  I2 ≤ 1.45·IZ",
            passed=bool(overload_ok),
            detail=(
                f"IB={ib:.2f} ≤ In={in_a:g} ≤ IZ={iz_final:.2f}; "
                f"I2={i2_a:.2f} ≤ 1.45·IZ={1.45 * iz_final:.2f} "
                f"(req_IZ≥{req_iz:.2f})"
            ),
            citations=[coord_cite],
        ),
        Check(
            name="voltage_drop",
            condition=f"ΔU% ≤ {vd_limit:g}%",
            passed=du_pct_final <= vd_limit + EPS,
            detail=f"ΔU={du_pct_final:.2f}% (предел {vd_limit:g}%)",
            citations=[pack.citation("voltage_drop")],
        ),
    ]
    warnings: list[str] = []
    if s_min_sc is None:
        checks.append(Check(
            name="short_circuit", condition="S ≥ I_scc·√t / k",
            passed=True, detail="пропущено: I_scc не задан",
            citations=[pack.citation("sc_adiabatic")]))
        warnings.append("Проверка КЗ пропущена: I_scc не задан.")
    else:
        checks.append(Check(
            name="short_circuit", condition="S ≥ I_scc·√t / k",
            passed=s_final >= s_min_sc - EPS,
            detail=f"S={s_final:g} мм² ≥ S_min={s_min_sc:.2f} мм²",
            citations=[pack.citation("sc_adiabatic")]))

    math_passed = passed and all(c.passed for c in checks)
    used_sections = [
        "standard_ratings",
        "standard_sections",
        "device_parameters",
        "overload_rule",
        "ampacity",
        "ambient_correction",
        "grouping_correction",
        "adiabatic_k",
        "resistivity",
        "reactance",
    ]
    if prot.max_voltage_drop_pct is None:
        used_sections.append("voltage_drop_limit")
    data_provenance = pack.assess_provenance(used_sections)
    overall: OverallStatus = "PASS" if math_passed else "FAIL"
    if overall == "PASS" and data_provenance.verification_status == "NEEDS_REVIEW":
        overall = "NEEDS_REVIEW"
        warnings.append(
            "Данные норм-пакета требуют проверки; арифметический PASS понижен до NEEDS_REVIEW."
        )

    # -- governing constraint --
    idx: dict[Governing, int] = {}
    if s_amp is not None:
        idx["overload_coordination"] = sections.index(s_amp)
    if s_vd is not None:
        idx["voltage_drop"] = sections.index(s_vd)
    if s_sc is not None:
        idx["short_circuit"] = sections.index(s_sc)
    if math_passed and idx:
        maxidx = max(idx.values())
        governing: Governing = next(n for n in _PRIORITY if idx.get(n) == maxidx)
    else:
        failing = [c.name for c in checks if not c.passed]
        governing = next((n for n in _PRIORITY if n in failing), "overload_coordination")

    summary_step = ReasoningStep(
        id="summary",
        title="Итог подбора",
        inputs={"required_iz_a": round(req_iz, 3), "vd_limit_pct": vd_limit,
                "s_amp": s_amp, "s_vd": s_vd, "s_sc": s_sc},
        formula="S = наименьшее сечение, проходящее все активные проверки",
        computation=(
            f"S={s_final:g} мм², In={in_a:g} A, IZ={iz_final:.2f} A; "
            f"связывает: {governing}; статус: {overall}"
        ),
        result={"section_mm2": s_final, "In_a": in_a, "Iz_a": round(iz_final, 3),
                "governing": governing, "overall": overall},
        citations=[coord_cite],
        status=("pass" if overall == "PASS" else "warning" if overall == "NEEDS_REVIEW" else "fail"),
    )

    trace: list[ReasoningStep] = [
        step_current, step_protection,
        chosen["amp_step"],  # type: ignore[list-item]
        chosen["vd_step"],   # type: ignore[list-item]
        step_sc, summary_step,
    ]

    note = provenance_note_for(data_provenance)
    return SizingResult(
        request=request,
        selected_cable=SelectedCable(
            cross_section_mm2=s_final, material=cond.material, insulation=cond.insulation,
            It_a=float(chosen["it"]), correction_total=round(float(chosen["prod_k"]), 4),  # type: ignore[arg-type]
            Iz_a=round(iz_final, 3), governing_constraint=governing,
        ),
        selected_protection=SelectedProtection(
            device_class=prot.device_class, In_a=in_a,
            I2_over_In=i2_ratio, I2_a=round(i2_a, 3),
        ),
        checks=checks,
        audit_trace=trace,
        design_current_a=round(ib, 4),
        voltage_drop_pct=round(du_pct_final, 3),
        voltage_drop_limit_pct=vd_limit,
        adiabatic_min_mm2=(round(s_min_sc, 3) if s_min_sc is not None else None),
        overall_status=overall,
        warnings=warnings,
        data_pack=pack.meta,
        data_provenance=data_provenance,
        data_provenance_note=note,
        disclaimer=DISCLAIMER,
    )


__all__ = ["size", "Citation"]
