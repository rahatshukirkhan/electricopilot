"""Design current IB from load (docs/01 §1.2)."""
from __future__ import annotations

import math

from ..data.loader import DataPack
from ..models import LoadSpec, ReasoningStep


def design_current(load: LoadSpec, pack: DataPack) -> tuple[float, ReasoningStep]:
    cite = pack.citation("IB_load")
    if load.current_a is not None:
        ib = load.current_a
        formula = "IB = I (задан ток)"
        comp = f"IB = {ib:g} A"
    elif load.phases == 1:
        ib = load.power_w / (load.voltage_v * load.power_factor)  # type: ignore[operator]
        formula = "IB = P / (U · cosφ)"
        comp = f"IB = {load.power_w:g} / ({load.voltage_v:g} · {load.power_factor:g}) = {ib:.2f} A"
    else:
        ib = load.power_w / (math.sqrt(3) * load.voltage_v * load.power_factor)  # type: ignore[operator]
        formula = "IB = P / (√3 · U · cosφ)"
        comp = (
            f"IB = {load.power_w:g} / (√3 · {load.voltage_v:g} · {load.power_factor:g}) "
            f"= {ib:.2f} A"
        )
    step = ReasoningStep(
        id="current",
        title="Расчётный ток IB",
        inputs={
            "power_w": load.power_w,
            "current_a": load.current_a,
            "voltage_v": load.voltage_v,
            "phases": load.phases,
            "power_factor": load.power_factor,
        },
        formula=formula,
        computation=comp,
        result={"IB_a": round(ib, 4)},
        citations=[cite],
        status="info",
    )
    return ib, step
