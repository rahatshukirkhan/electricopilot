"""Frozen pydantic contract for ElectriCopilot (docs/03).

The numeric core produces these types; LLM/report/CLI consume them. Any change here
must be reflected in docs/03 first (spec-first invariant).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# --- enums / aliases (docs/03 §3.1) ---
Phase = Literal[1, 3]
Material = Literal["Cu", "Al"]
Insulation = Literal["PVC", "XLPE"]
InstallMethod = Literal["A1", "A2", "B1", "B2", "C", "D", "E", "F", "G"]
DeviceClass = Literal["MCB", "MCCB", "gG_fuse"]
CircuitPurpose = Literal["lighting", "power", "socket", "motor", "general"]
StepStatus = Literal["info", "pass", "fail", "warning"]
OverallStatus = Literal["PASS", "FAIL", "NEEDS_REVIEW"]
DataStatus = Literal["illustrative", "licensed"]
SignStatus = Literal["UNSIGNED_ADVISORY", "SIGNED"]
Governing = Literal["overload_coordination", "voltage_drop", "short_circuit"]
StepId = Literal["current", "protection", "ampacity", "voltage_drop", "short_circuit", "summary"]
CheckName = Literal["overload_coordination", "voltage_drop", "short_circuit"]
CitationKey = Literal[
    "IB_load", "In_selection", "coord_overload", "voltage_drop", "sc_adiabatic", "install_method"
]


# --- traceability (docs/03 §3.2) ---
class Citation(BaseModel):
    standard: str
    clause: Optional[str] = None
    table: Optional[str] = None
    edition: Optional[str] = None
    note: Optional[str] = None


# --- input (docs/03 §3.3) ---
class LoadSpec(BaseModel):
    description: Optional[str] = None
    power_w: Optional[float] = Field(None, gt=0)
    current_a: Optional[float] = Field(None, gt=0)
    voltage_v: float = Field(..., gt=0)
    phases: Phase = 1
    power_factor: float = Field(0.9, gt=0, le=1)
    purpose: CircuitPurpose = "general"

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "LoadSpec":
        if (self.power_w is None) == (self.current_a is None):
            raise ValueError("provide exactly one of {power_w, current_a}")
        return self


class InstallationConditions(BaseModel):
    method: InstallMethod
    material: Material = "Cu"
    insulation: Insulation = "PVC"
    ambient_temp_c: float = 30.0
    grouping_circuits: int = Field(1, ge=1)
    length_m: float = Field(..., gt=0)
    loaded_conductors: Optional[Literal[2, 3]] = None


class ProtectionSpec(BaseModel):
    device_class: DeviceClass = "MCB"
    prospective_fault_current_a: Optional[float] = Field(None, gt=0)
    disconnection_time_s: float = Field(0.1, gt=0)
    max_voltage_drop_pct: Optional[float] = None


class SizingRequest(BaseModel):
    load: LoadSpec
    installation: InstallationConditions
    protection: ProtectionSpec = ProtectionSpec()
    project_ref: Optional[str] = None
    designer: Optional[str] = None


# --- trace & checks (docs/03 §3.4) ---
class ReasoningStep(BaseModel):
    id: StepId
    title: str
    inputs: dict[str, Any]
    formula: Optional[str] = None
    computation: Optional[str] = None
    result: dict[str, Any]
    citations: list[Citation] = Field(default_factory=list)
    status: StepStatus = "info"


class Check(BaseModel):
    name: CheckName
    condition: str
    passed: bool
    detail: str
    citations: list[Citation] = Field(default_factory=list)


# --- selection & result (docs/03 §3.5) ---
class SelectedCable(BaseModel):
    cross_section_mm2: float
    material: Material
    insulation: Insulation
    It_a: float
    correction_total: float
    Iz_a: float
    governing_constraint: Governing


class SelectedProtection(BaseModel):
    device_class: DeviceClass
    In_a: float
    I2_over_In: float
    I2_a: float


class SignOff(BaseModel):
    status: SignStatus = "UNSIGNED_ADVISORY"
    engineer_name: Optional[str] = None
    license_id: Optional[str] = None
    signed_at: Optional[str] = None
    statement: str = (
        "Рекомендательный расчёт. НЕ является сертификацией. Требуется проверка и "
        "подпись квалифицированного инженера перед применением."
    )


class DataPackMeta(BaseModel):
    name: str
    version: str
    status: DataStatus
    source_note: str


class SizingResult(BaseModel):
    request: SizingRequest
    selected_cable: SelectedCable
    selected_protection: SelectedProtection
    checks: list[Check]
    audit_trace: list[ReasoningStep]
    design_current_a: float
    voltage_drop_pct: float
    voltage_drop_limit_pct: float
    adiabatic_min_mm2: Optional[float]
    overall_status: OverallStatus
    warnings: list[str] = Field(default_factory=list)
    data_pack: DataPackMeta
    data_provenance_note: str
    signoff: SignOff = Field(default_factory=SignOff)
    disclaimer: str


# --- LLM contracts (docs/03 §3.6) ---
class LlmNarrative(BaseModel):
    text: str
    model: str = ""
    provenance_ok: bool = True
    unverified_numbers: list[str] = Field(default_factory=list)


class VerificationVerdict(BaseModel):
    agrees: bool
    issues: list[str] = Field(default_factory=list)
    model: str = ""
    deterministic_ok: bool = True


AuditTrace = list[ReasoningStep]

# Canonical constant text reused across report/guardrails.
DISCLAIMER = (
    "⚠️  Рекомендательный расчёт ElectriCopilot. НЕ является сертификацией и не заменяет "
    "проектную документацию. Требуется проверка и подпись квалифицированного инженера."
)
PROVENANCE_NOTE_ILLUSTRATIVE = (
    "Числовые значения таблиц норм-пакета — СИНТЕТИЧЕСКИЕ (не выведены из IEC) и подлежат "
    "замене лицензионными данными. Механизм цитирования реален (ссылки на пункты/таблицы "
    "IEC 60364), но пометки PASS/FAIL отражают арифметику относительно синтетических значений, "
    "а не соответствие реальному стандарту."
)
