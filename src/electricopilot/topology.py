"""Typed project-topology validation and shared deterministic board formulae (docs/11)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field, ValidationError, field_validator

from .exceptions import ProjectTopologyError
from .models import Phase, SizingRequest

PhaseAssignment = Literal["L1", "L2", "L3", "L1L2L3"]


class SupplyTopology(BaseModel):
    """Board-level supply fields; omitted values retain the legacy 400 V / 3-phase default."""

    voltage_v: float = Field(400.0, gt=0)
    phases: Phase = 3

    @field_validator("voltage_v", mode="before")
    @classmethod
    def _typed_voltage(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        return value

    @field_validator("phases", mode="before")
    @classmethod
    def _typed_phases(cls, value: Any) -> Any:
        if type(value) is not int or value not in (1, 3):
            raise ValueError("must be integer 1 or 3")
        return value


@dataclass(frozen=True)
class CircuitTopology:
    request: SizingRequest
    phase: PhaseAssignment


@dataclass(frozen=True)
class ProjectTopology:
    supply: SupplyTopology
    circuits: tuple[CircuitTopology, ...]


def _project_error(message: str) -> ProjectTopologyError:
    return ProjectTopologyError(message)


def _circuit_label(raw: dict[str, Any], index: int) -> str:
    return str(raw.get("ref") or raw.get("id") or f"#{index + 1}")


def _phase_assignment(
    supply: SupplyTopology,
    request: SizingRequest,
    meta: dict[str, Any],
    label: str,
) -> PhaseAssignment:
    raw_phase = meta.get("phase")
    if request.load.phases == 3:
        if supply.phases != 3:
            raise _project_error(f"Цепь {label}: 3ф нагрузка несовместима с 1ф питанием щита.")
        if request.load.voltage_v != supply.voltage_v:
            raise _project_error(
                f"Цепь {label}: voltage_v 3ф нагрузки должен совпадать с supply.voltage_v."
            )
        phase: PhaseAssignment = "L1L2L3" if raw_phase is None else raw_phase
        if phase != "L1L2L3":
            raise _project_error(f"Цепь {label}: 3ф нагрузка требует meta.phase=L1L2L3.")
        return phase

    phase = "L1" if raw_phase is None else raw_phase
    allowed = ("L1",) if supply.phases == 1 else ("L1", "L2", "L3")
    if phase not in allowed:
        rendered = "L1" if supply.phases == 1 else "L1, L2 или L3"
        raise _project_error(f"Цепь {label}: 1ф нагрузка требует meta.phase={rendered}.")
    if supply.phases == 1 and request.load.voltage_v != supply.voltage_v:
        raise _project_error(
            f"Цепь {label}: voltage_v 1ф нагрузки должен совпадать с supply.voltage_v."
        )
    return phase


def validate_project_topology(project: dict[str, Any]) -> ProjectTopology:
    """Validate board topology once before any partial sizing/report calculation starts."""
    raw_supply = project.get("supply", {})
    if not isinstance(raw_supply, dict):
        raise _project_error("Поле project.supply должно быть объектом.")
    try:
        supply = SupplyTopology.model_validate(raw_supply)
    except ValidationError as exc:
        problem = exc.errors(include_url=False)[0]
        location = ".".join(str(part) for part in problem["loc"])
        raise _project_error(f"Некорректное project.supply.{location}: {problem['msg']}.") from None

    raw_circuits = project.get("circuits", [])
    if not isinstance(raw_circuits, list):
        raise _project_error("Поле project.circuits должно быть массивом.")
    validated: list[CircuitTopology] = []
    for index, raw in enumerate(raw_circuits):
        if not isinstance(raw, dict):
            raise _project_error(f"Цепь #{index + 1} должна быть объектом.")
        label = _circuit_label(raw, index)
        try:
            request = SizingRequest.model_validate(raw.get("request"))
        except ValidationError as exc:
            problem = exc.errors(include_url=False)[0]
            location = ".".join(str(part) for part in problem["loc"])
            raise _project_error(
                f"Цепь {label}: некорректный request.{location}: {problem['msg']}."
            ) from None
        meta = raw.get("meta") or {}
        if not isinstance(meta, dict):
            raise _project_error(f"Цепь {label}: meta должно быть объектом.")
        validated.append(CircuitTopology(
            request=request,
            phase=_phase_assignment(supply, request, meta, label),
        ))
    return ProjectTopology(supply=supply, circuits=tuple(validated))


def incomer_current_a(apparent_power_kva: float, supply: SupplyTopology) -> float:
    """Demand current: S/U for one phase, S/(sqrt(3)*U) for three phases."""
    phase_factor = 1.0 if supply.phases == 1 else math.sqrt(3)
    return apparent_power_kva * 1000 / (phase_factor * supply.voltage_v)


def feeder_current_a(phase_currents: Mapping[str, float], supply: SupplyTopology) -> float:
    """Current used by the feeder drop calculation for the active board topology."""
    if supply.phases == 1:
        return phase_currents["L1"]
    return max(phase_currents[name] for name in ("L1", "L2", "L3"))


def feeder_voltage_drop_pct(
    current_a: float,
    length_m: float,
    section_mm2: float,
    resistivity_ohm_mm2_m: float,
    supply: SupplyTopology,
) -> float:
    """Resistive feeder drop with the topology factor defined in docs/01 section 1.6."""
    phase_factor = 2.0 if supply.phases == 1 else math.sqrt(3)
    resistance = resistivity_ohm_mm2_m * length_m / section_mm2
    return phase_factor * current_a * resistance / supply.voltage_v * 100


def phase_imbalance_pct(
    phase_currents: Mapping[str, float], supply: SupplyTopology,
) -> float | None:
    """Three-phase imbalance; structurally inapplicable to a one-phase board."""
    if supply.phases == 1:
        return None
    values = [phase_currents[name] for name in ("L1", "L2", "L3")]
    average = sum(values) / 3 if any(values) else 0.0
    return ((max(values) - min(values)) / average * 100) if average > 0 else 0.0
