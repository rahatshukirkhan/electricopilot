"""Voltage drop ΔU (docs/01 §1.6, IEC 60364-5-52 Annex G — informative)."""
from __future__ import annotations

import math

from ..data.loader import DataPack
from ..models import InstallationConditions, LoadSpec, ReasoningStep


def voltage_drop(
    load: LoadSpec, cond: InstallationConditions, size_mm2: float, ib: float, pack: DataPack
) -> tuple[float, float, ReasoningStep]:
    rho = pack.resistivity(cond.material)
    lam = pack.reactance()
    pf = load.power_factor
    sinphi = math.sqrt(max(0.0, 1.0 - pf * pf))
    term = rho * pf / size_mm2 + lam * sinphi
    if load.phases == 1:
        du = 2 * ib * cond.length_m * term
        formula = "ΔU = 2·IB·L·(ρ·cosφ/S + λ·sinφ)"
    else:
        du = math.sqrt(3) * ib * cond.length_m * term
        formula = "ΔU = √3·IB·L·(ρ·cosφ/S + λ·sinφ)"
    du_pct = du / load.voltage_v * 100.0
    step = ReasoningStep(
        id="voltage_drop",
        title="Падение напряжения ΔU",
        inputs={"section_mm2": size_mm2, "length_m": cond.length_m, "IB_a": round(ib, 3),
                "rho": rho, "reactance": lam, "cosphi": pf, "phases": load.phases},
        formula=formula,
        computation=f"ΔU = {du:.2f} В → {du_pct:.2f} % от {load.voltage_v:g} В",
        result={"drop_v": round(du, 3), "drop_pct": round(du_pct, 3)},
        citations=[pack.citation("voltage_drop")],
        status="info",
    )
    return du, du_pct, step
