"""Corrected current-carrying capacity IZ = It · ∏k (docs/01 §1.4)."""
from __future__ import annotations

from ..data.loader import DataPack
from ..models import InstallationConditions, ReasoningStep


def corrected_ampacity(
    size_mm2: float, cond: InstallationConditions, pack: DataPack
) -> tuple[float, float, float, ReasoningStep]:
    """Return (It, ∏k, Iz, step). cond.loaded_conductors must be resolved (non-None)."""
    n = cond.loaded_conductors or 2  # sizer resolves this from phases before calling
    it, it_cite = pack.ampacity_it(cond.method, cond.material, cond.insulation, int(n), size_mm2)
    ka, ka_cite = pack.ambient_factor(cond.insulation, cond.ambient_temp_c)
    kg, kg_cite = pack.grouping_factor(cond.grouping_circuits)
    prod_k = ka * kg
    iz = it * prod_k
    step = ReasoningStep(
        id="ampacity",
        title="Пропускная способность IZ = It · ka · kg",
        inputs={
            "section_mm2": size_mm2,
            "method": cond.method,
            "material": cond.material,
            "insulation": cond.insulation,
            "loaded_conductors": int(n),
            "ambient_temp_c": cond.ambient_temp_c,
            "grouping_circuits": cond.grouping_circuits,
        },
        formula="IZ = It · ka · kg",
        computation=f"IZ = {it:g} · {ka:g} · {kg:g} = {iz:.2f} A",
        result={"It_a": it, "ka": ka, "kg": kg, "correction_total": round(prod_k, 4),
                "Iz_a": round(iz, 3)},
        citations=[it_cite, ka_cite, kg_cite],
        status="info",
    )
    return it, prod_k, iz, step
