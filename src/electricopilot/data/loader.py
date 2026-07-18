"""Norm data pack: typed model, accessors, loader (docs/03 §3.7, docs/04).

The engine touches pack data ONLY through DataPack accessors — this pins key
normalization (pack_key) and off-table policy in one place.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..exceptions import DataPackError
from ..models import (
    Citation,
    CitationKey,
    CircuitPurpose,
    DataPackMeta,
    DataProvenanceSummary,
    DataSectionAssessment,
    DataSourceRecord,
    DeviceClass,
    InstallMethod,
    Insulation,
    Material,
)

DEFAULT_PACK_NAME = "iec-stub"
PACKS_SUBDIR = "data/packs"
NUMERIC_PROVENANCE_SECTIONS = (
    "standard_ratings",
    "standard_sections",
    "device_parameters",
    "overload_rule",
    "ampacity",
    "ambient_correction",
    "grouping_correction",
    "adiabatic_k",
    "voltage_drop_limit",
    "resistivity",
    "reactance",
)


def pack_key(x: float | int) -> str:
    """Canonical numeric table key: 4.0->'4', 2.5->'2.5', 35.0->'35'."""
    return format(float(x), "g")


def _cite(d: dict[str, Any]) -> Citation:
    return Citation(**{k: v for k, v in d.items() if k in Citation.model_fields})


class DataPack(BaseModel):
    meta: DataPackMeta
    provenance: dict[str, DataSourceRecord] = Field(default_factory=dict)
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

    def has_ampacity(
        self, method: InstallMethod, material: Material, insulation: Insulation,
        n: int, size_mm2: float,
    ) -> bool:
        """True iff this exact section is present in the ampacity table for the given
        method/material/insulation/n. Lets the sizer skip sections a real pack doesn't
        cover (e.g. pue-rk's Табл.4/5 stop short of 300 mm² for in-conduit) while still
        letting ambient/grouping off-table errors surface as hard failures (docs/12 §1.1)."""
        try:
            table = self.ampacity[method][material][insulation][str(n)]
        except (KeyError, TypeError):
            return False
        return pack_key(size_mm2) in table

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

    def _source_record(self, section: str) -> DataSourceRecord:
        """Return an explicit section record, or a conservative meta-derived fallback."""
        source = self.provenance.get(section)
        if source is not None:
            return source
        return DataSourceRecord(
            origin=self.meta.status,
            source_document=self.meta.source_document,
            note=self.meta.source_note,
        )

    def assess_provenance(self, sections: list[str]) -> DataProvenanceSummary:
        """Assess only the numeric families actually consumed by a result."""
        assessments: list[DataSectionAssessment] = []
        seen: set[str] = set()
        for section in sections:
            if section in seen:
                continue
            seen.add(section)
            source = self._source_record(section)
            issues: list[str] = []
            if source.origin not in ("public_standard", "licensed"):
                issues.append(f"origin={source.origin}")
            if not source.source_document:
                issues.append("source_document missing")
            if source.origin == "public_standard" and not source.source_url:
                issues.append("source_url missing")
            if not source.entered_by:
                issues.append("entered_by missing")
            if not source.verified_by:
                issues.append("verified_by missing")
            if not source.verified_at:
                issues.append("verified_at missing")
            assessments.append(DataSectionAssessment(
                section=section,
                source=source,
                trusted=not issues,
                issues=issues,
            ))
        untrusted = [a.section for a in assessments if not a.trusted]
        return DataProvenanceSummary(
            pack_name=self.meta.name,
            pack_version=self.meta.version,
            pack_origin=self.meta.status,
            verification_status="NEEDS_REVIEW" if untrusted else "VERIFIED",
            used_sections=assessments,
            untrusted_sections=untrusted,
        )

    def publication_assessment(self) -> DataProvenanceSummary:
        """Assess every numeric family required for a publishable sizing pack."""
        return self.assess_provenance(list(NUMERIC_PROVENANCE_SECTIONS))


def _packs_dir() -> Path:
    """Resolve data/packs/ on disk (Vercel serverless: loader.py sits next to data/)."""
    return Path(__file__).parent / "packs"


@lru_cache(maxsize=None)
def _load_pack_by_name(name: str) -> DataPack:
    """Load + validate a bundled pack by name, cached for the process lifetime.

    Safe to cache: bundled packs ship with the package (immutable at runtime) and the
    engine only reads them through DataPack accessors, so one shared instance per name
    is fine. lru_cache does not cache exceptions, so a failed load is retried next call.
    """
    try:
        raw = resources.files("electricopilot").joinpath(
            f"{PACKS_SUBDIR}/{name}.json"
        ).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, TypeError, AttributeError):
        # bundled-but-not-installed runtime (e.g. Vercel serverless): loader.py sits in
        # the same dir as data/, so resolve relative to __file__.
        raw = (_packs_dir() / f"{name}.json").read_text(encoding="utf-8")
    return DataPack(**json.loads(raw))


def _is_pack_name(path: str | Path) -> bool:
    """Bare pack name (no path separator, no .json suffix) resolves from data/packs/;
    anything else is treated as a filesystem path (docs/12 §1.1)."""
    s = str(path)
    return "/" not in s and not s.endswith(".json")


def load_data_pack(path: str | Path | None = None) -> DataPack:
    """Load and validate a norm data pack. Bundled packs (bare name, e.g. 'pue-rk') resolve
    via importlib.resources (independent of CWD) and are cached for the process; a
    filesystem path (contains '/' or ends in .json) always loads fresh."""
    try:
        if path is None:
            return _load_pack_by_name(DEFAULT_PACK_NAME)
        if _is_pack_name(path):
            return _load_pack_by_name(str(path))
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return DataPack(**data)
    except DataPackError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap any load/parse/validate failure
        raise DataPackError(f"failed to load data pack from {path}: {exc}") from exc


def list_packs() -> list[DataPackMeta]:
    """Enumerate bundled packs in data/packs/ (docs/12 §1.1), for GET /api/packs and CLI."""
    try:
        names = sorted(
            p.name.removesuffix(".json")
            for p in resources.files("electricopilot").joinpath(PACKS_SUBDIR).iterdir()
            if p.name.endswith(".json")
        )
    except (FileNotFoundError, ModuleNotFoundError, TypeError, AttributeError, NotADirectoryError):
        names = sorted(p.stem for p in _packs_dir().glob("*.json"))
    return [_load_pack_by_name(n).meta for n in names]
