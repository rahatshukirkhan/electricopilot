"""Protective-device rating In from the discrete standard series (docs/01 §1.3)."""
from __future__ import annotations

from ..data.loader import DataPack
from ..models import DeviceClass, ReasoningStep


def select_rating(ib: float, device: DeviceClass, pack: DataPack) -> tuple[float, ReasoningStep]:
    in_a, cite = pack.rating_for(ib)
    i2_ratio, _ = pack.i2_over_in(device)
    step = ReasoningStep(
        id="protection",
        title="Номинал аппарата защиты In",
        inputs={"IB_a": round(ib, 4), "device_class": device, "I2_over_In": i2_ratio},
        formula="In = наименьший стандартный номинал ≥ IB",
        computation=f"In = {in_a:g} A — наименьший стандартный номинал ≥ IB={ib:.2f} A",
        result={"In_a": in_a, "I2_a": round(i2_ratio * in_a, 3)},
        citations=[cite],
        status="info",
    )
    return in_a, step
