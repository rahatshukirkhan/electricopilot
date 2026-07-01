"""Norm data pack: typed model, accessors, loader (docs/03 §3.7, docs/04).

The engine touches pack data ONLY through DataPack accessors — this pins key
normalization (pack_key) and off-table policy in one place.
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator

from ..exceptions import DataPackError
from ..models import (
    Citation,
    CitationKey,
    CircuitPurpose,
    DataPackMeta,
    DeviceClass,
    InstallMethod,
    Insulation,
    Material,
)

DEFAULT_PACK_PATH = "electricopilot/data/iec_stub.json"


def pack_key(x: float | int) -> str:
    """Canonical numeric table key: 4.0->'4', 2.5->'2.5', 35.0->'35'."""
    return format(float(x), "g")


def _cite(d: dict[str, Any]) -> Citation:
    return Citation(**{k: v for k, v in d.items() if k in Citation.model_fields})


class DataPack(BaseModel):
    meta: DataPackMeta
    standard_ratings_a: list[float]
    standard_sections_mm2: list[float]
    device_classes: dict[str, Any]
    k_material: dict[str, Any]
    resistivity_ohm_mm2_per_m: dict[str, float]
    reactance_ohm_per_m: float
    vd_limits_pct: dict[str, float]
    reference_ambient_c: dict[str, float]
    ambient_correction: dict[str, Any]
    grouping_correction: dict[str, Any]
    ampacity: dict[str, Any]
    citations: dict[str, Any]
    trip_curves: dict[str, Any] = {}   # illustrative time-current curves (Studio TCC, docs/10)

    @model_validator(mode="after")
    def _validate(self) -> "DataPack":
        if sorted(self.standard_ratings_a) != self.standard_ratings_a:
            raise DataPackError("standard_ratings_a must be ascending")
        if sorted(self.standard_sections_mm2) != self.standard_sections_mm2:
            raise DataPackError("standard_sections_mm2 must be ascending")
        for key in ("IB_load", "In_selection", "coord_overload", "voltage_drop",
                    "sc_adiabatic", "install_method"):
            if key not in self.citations:
                raise DataPackError(f"citations registry missing key '{key}'")
        return self

    # -- accessors --
    def ratings(self) -> list[float]:
        return self.standard_ratings_a

    def sections(self) -> list[float]:
        return self.standard_sections_mm2

    def rating_for(self, ib: float) -> tuple[float, Citation]:
        for r in self.standard_ratings_a:
            if r >= ib:
                return r, self.citation("In_selection")
        raise DataPackError(f"no standard rating >= {ib} A in series")

    def i2_over_in(self, device: DeviceClass) -> tuple[float, Citation]:
        entry = self.device_classes.get(device)
        if entry is None:
            raise DataPackError(f"device class '{device}' not in pack")
        return float(entry["i2_over_in"]), _cite(entry.get("citation", {"standard": "n/a"}))

    def ampacity_it(
        self, method: InstallMethod, material: Material, insulation: Insulation,
        n: int, size_mm2: float,
    ) -> tuple[float, Citation]:
        try:
            table = self.ampacity[method][material][insulation][str(n)]
        except (KeyError, TypeError):
            raise DataPackError(
                f"no ampacity table for method={method}/{material}/{insulation}/{n} conductors"
            ) from None
        k = pack_key(size_mm2)
        if k not in table:
            raise DataPackError(
                f"section {size_mm2} mm2 not in ampacity table {method}/{material}/{insulation}/{n}"
            )
        return float(table[k]), _cite(self.ampacity.get("_citation", {"standard": "n/a"}))

    def ambient_factor(self, insulation: Insulation, ambient_c: float) -> tuple[float, Citation]:
        table = self.ambient_correction.get(insulation)
        if not isinstance(table, dict):
            raise DataPackError(f"no ambient correction for insulation '{insulation}'")
        k = pack_key(ambient_c)
        if k not in table:
            allowed = ", ".join(kk for kk in table if kk != "_citation")
            raise DataPackError(
                f"ambient {ambient_c} C off-table for {insulation} (allowed: {allowed})"
            )
        return float(table[k]), _cite(self.ambient_correction.get("_citation", {"standard": "n/a"}))

    def grouping_factor(self, circuits: int) -> tuple[float, Citation]:
        k = pack_key(circuits)
        if k not in self.grouping_correction or k == "_citation":
            allowed = ", ".join(kk for kk in self.grouping_correction if kk != "_citation")
            raise DataPackError(f"grouping {circuits} off-table (allowed: {allowed})")
        return float(self.grouping_correction[k]), _cite(
            self.grouping_correction.get("_citation", {"standard": "n/a"})
        )

    def k_adiabatic(self, material: Material, insulation: Insulation) -> tuple[float, Citation]:
        key = f"{material}/{insulation}"
        if key not in self.k_material:
            raise DataPackError(f"no adiabatic k for '{key}'")
        return float(self.k_material[key]), _cite(
            self.k_material.get("_citation", {"standard": "n/a"})
        )

    def resistivity(self, material: Material) -> float:
        if material not in self.resistivity_ohm_mm2_per_m:
            raise DataPackError(f"no resistivity for '{material}'")
        return self.resistivity_ohm_mm2_per_m[material]

    def reactance(self) -> float:
        return self.reactance_ohm_per_m

    def vd_limit(self, purpose: CircuitPurpose) -> float:
        if purpose not in self.vd_limits_pct:
            raise DataPackError(f"no vd limit for purpose '{purpose}'")
        return self.vd_limits_pct[purpose]

    def citation(self, key: CitationKey) -> Citation:
        if key not in self.citations:
            raise DataPackError(f"no citation for key '{key}'")
        return _cite(self.citations[key])

    def trip_curve(self, device: DeviceClass) -> tuple[dict[str, Any], Citation]:
        entry = self.trip_curves.get(device)
        if entry is None:
            raise DataPackError(f"no trip curve for device class '{device}'")
        return entry, _cite(entry.get("citation", {"standard": "n/a"}))


def load_data_pack(path: str | Path = DEFAULT_PACK_PATH) -> DataPack:
    """Load and validate a norm data pack. Default resolves via importlib.resources,
    independent of CWD; a custom path loads from the filesystem."""
    try:
        if str(path) == DEFAULT_PACK_PATH:
            raw = resources.files("electricopilot").joinpath("data/iec_stub.json").read_text(
                encoding="utf-8"
            )
        else:
            raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        return DataPack(**data)
    except DataPackError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap any load/parse/validate failure
        raise DataPackError(f"failed to load data pack from {path}: {exc}") from exc
