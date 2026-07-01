"""Adiabatic short-circuit withstand: S ≥ I·√t / k (docs/01 §1.7)."""
from __future__ import annotations

import math

from ..data.loader import DataPack
from ..models import InstallationConditions, ProtectionSpec, ReasoningStep


def adiabatic_min_section(
    prot: ProtectionSpec, cond: InstallationConditions, pack: DataPack
) -> tuple[float | None, ReasoningStep]:
    k, k_cite = pack.k_adiabatic(cond.material, cond.insulation)
    if prot.prospective_fault_current_a is None:
        step = ReasoningStep(
            id="short_circuit",
            title="Термическая стойкость к КЗ (адиабатика)",
            inputs={"prospective_fault_current_a": None, "k": k},
            formula="S ≥ I_scc·√t / k",
            computation="пропущено: I_scc не задан",
            result={"S_min_mm2": None},
            citations=[pack.citation("sc_adiabatic"), k_cite],
            status="warning",
        )
        return None, step
    iscc = prot.prospective_fault_current_a
    t = prot.disconnection_time_s
    s_min = iscc * math.sqrt(t) / k
    step = ReasoningStep(
        id="short_circuit",
        title="Термическая стойкость к КЗ (адиабатика)",
        inputs={"prospective_fault_current_a": iscc, "disconnection_time_s": t,
                "material": cond.material, "insulation": cond.insulation, "k": k},
        formula="S_min = I_scc·√t / k",
        computation=f"S_min = {iscc:g}·√{t:g} / {k:g} = {s_min:.2f} мм²",
        result={"S_min_mm2": round(s_min, 3)},
        citations=[pack.citation("sc_adiabatic"), k_cite],
        status="info",
    )
    return s_min, step
