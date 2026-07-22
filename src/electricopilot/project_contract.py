"""Versioned project contract and pure legacy migration (docs/11)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Literal, Mapping, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .exceptions import ProjectContractError
from .models import (
    CircuitPurpose,
    DeviceClass,
    Governing,
    InstallMethod,
    Insulation,
    Material,
    OverallStatus,
    Phase,
    SignStatus,
    SizingRequest,
)

CURRENT_PROJECT_SCHEMA_VERSION = 2
PhaseAssignment = Literal["L1", "L2", "L3", "L1L2L3"]


class _ProjectModel(BaseModel):
    """Strict, closed models for fields owned by the project contract."""

    model_config = ConfigDict(extra="forbid", strict=True)


class RcdSettings(_ProjectModel):
    present: bool = False
    ma: float | None = Field(default=None, gt=0)
    type: str | None = None


class IncomerSettings(_ProjectModel):
    device_class: DeviceClass | None = None
    In_a: float | None = Field(default=None, gt=0)


class FeederSettings(_ProjectModel):
    length_m: float = Field(gt=0)
    section_mm2: float = Field(gt=0)
    material: Material


class ProjectSupply(_ProjectModel):
    voltage_v: float = Field(400.0, gt=0)
    phases: Phase = 3
    ways_total: int = Field(12, ge=1)
    earthing: str = "TN-C-S"
    incomer: IncomerSettings | None = None
    feeder: FeederSettings | None = None
    ipf_ka: float | None = Field(default=None, gt=0)
    method: InstallMethod = "C"
    material: Material = "Cu"
    insulation: Insulation = "PVC"
    ambient_temp_c: float = 30.0


class DiversitySettings(_ProjectModel):
    factors: dict[str, float] = Field(default_factory=dict)

    @field_validator("factors")
    @classmethod
    def _positive_factors(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not 0 < factor <= 1 for factor in value.values()):
            raise ValueError("diversity factors must be greater than 0 and at most 1")
        return value


class ExportSettings(_ProjectModel):
    cable_margin: float = Field(1.05, ge=1)


class CircuitImportDeclaration(_ProjectModel):
    """Original schedule values; audit evidence only, never a sizing input."""

    source_file: str = Field(min_length=1)
    source_sheet: str | None = None
    source_row: int = Field(ge=2)
    raw_values: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    declared_section_mm2: float | None = Field(default=None, gt=0)
    declared_in_a: float | None = Field(default=None, gt=0)
    assumed_fields: list[str] = Field(default_factory=list)
    mapping_confirmed: bool = False


class CircuitMeta(_ProjectModel):
    phase: PhaseAssignment | None = None
    rcd: RcdSettings = Field(default_factory=RcdSettings)
    diversity_category: CircuitPurpose | str | None = None
    cores: str | None = None
    pe_section_mm2: float | None = Field(default=None, gt=0)
    import_declaration: CircuitImportDeclaration | None = None


class CircuitResultSnapshot(_ProjectModel):
    """Untrusted UI cache; values are preserved but never consumed by the core."""

    status: str | None = None
    section: float | None = None
    In: float | None = None
    IB: float | None = None
    Iz: float | None = None
    vd: float | None = None
    governing: Governing | str | None = None


class CircuitSignoff(_ProjectModel):
    status: SignStatus = "UNSIGNED_ADVISORY"
    engineer_name: str | None = None
    license_id: str | None = None
    signed_at: str | None = None
    ack_illustrative: bool = False


class Circuit(_ProjectModel):
    id: str = Field(min_length=1)
    ref: str = ""
    sort_index: int = Field(ge=0)
    request: SizingRequest
    meta: CircuitMeta = Field(default_factory=CircuitMeta)
    result: CircuitResultSnapshot | None = None
    signoff: CircuitSignoff = Field(default_factory=CircuitSignoff)


class RollupCounts(_ProjectModel):
    PASS: int = Field(0, ge=0)
    FAIL: int = Field(0, ge=0)
    NEEDS_REVIEW: int = Field(0, ge=0)


class ProjectRollup(_ProjectModel):
    counts: RollupCounts = Field(default_factory=RollupCounts)
    status: OverallStatus = "PASS"


class ProjectImportInfo(_ProjectModel):
    source_file: str = Field(min_length=1)
    source_format: Literal["csv", "xlsx"]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping: dict[str, str | None]
    assumed_fields: list[str] = Field(default_factory=list)
    assumptions_confirmed: bool = False
    mapping_method: Literal["heuristic", "llm", "manual", "mixed"]


class Project(_ProjectModel):
    schema_version: Literal[2]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    board_ref: str = Field(min_length=1)
    location: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    norm_pack: str | None = None
    supply: ProjectSupply
    diversity: DiversitySettings = Field(default_factory=DiversitySettings)
    export_settings: ExportSettings = Field(default_factory=ExportSettings)
    circuits: list[Circuit] = Field(max_length=128)
    rollup: ProjectRollup | None = None
    import_info: ProjectImportInfo | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _valid_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("timestamp must be ISO-8601") from None
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, value: Any) -> Any:
        return migrate_project_payload(value)

    @field_validator("circuits")
    @classmethod
    def _unique_circuit_ids(cls, circuits: list[Circuit]) -> list[Circuit]:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for circuit in circuits:
            if circuit.id in seen:
                duplicates.add(circuit.id)
            seen.add(circuit.id)
        if duplicates:
            rendered = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate circuit id(s): {rendered}")
        return circuits


ProjectInput: TypeAlias = Project | Mapping[str, Any]


def migrate_project_payload(payload: Any) -> Any:
    """Copy and upgrade unversioned v1 payloads; never invent ids or engineering values."""
    if isinstance(payload, Project):
        return payload
    if not isinstance(payload, Mapping):
        return payload

    migrated = deepcopy(dict(payload))
    if "schema_version" in migrated:
        return migrated

    migrated["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
    supply = migrated.get("supply")
    if isinstance(supply, Mapping):
        migrated_supply = deepcopy(dict(supply))
        migrated_supply.setdefault("phases", 3)
        migrated_supply.setdefault("voltage_v", 400)
        migrated["supply"] = migrated_supply

    circuits = migrated.get("circuits")
    if isinstance(circuits, list):
        migrated_circuits: list[Any] = []
        for index, circuit in enumerate(circuits):
            if isinstance(circuit, Mapping):
                migrated_circuit = deepcopy(dict(circuit))
                migrated_circuit.setdefault("sort_index", index)
                migrated_circuits.append(migrated_circuit)
            else:
                migrated_circuits.append(deepcopy(circuit))
        migrated["circuits"] = migrated_circuits
    return migrated


def validate_project(payload: ProjectInput) -> Project:
    """Return the canonical typed project or a stable domain error for non-HTTP callers."""
    if isinstance(payload, Project):
        return payload
    try:
        return Project.model_validate(payload)
    except ValidationError as exc:
        problem = exc.errors(include_url=False)[0]
        path = ".".join(str(part) for part in problem["loc"])
        raise ProjectContractError(path=path, message=str(problem["msg"])) from None


def project_payload(project: ProjectInput) -> dict[str, Any]:
    """Stable JSON-compatible serialization shared by all project consumers."""
    return validate_project(project).model_dump(mode="json")
