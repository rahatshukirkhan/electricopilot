"""Frozen pydantic contract for ElectriCopilot (docs/03).

The numeric core produces these types; LLM/report/CLI consume them. Any change here
must be reflected in docs/03 first (spec-first invariant).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_serializer, model_validator

# --- enums / aliases (docs/03 §3.1) ---
Phase = Literal[1, 3]
Material = Literal["Cu", "Al"]
Insulation = Literal["PVC", "XLPE"]
InstallMethod = Literal["A1", "A2", "B1", "B2", "C", "D", "E", "F", "G"]
DeviceClass = Literal["MCB", "MCCB", "gG_fuse"]
CircuitPurpose = Literal["lighting", "power", "socket", "motor", "general"]
StepStatus = Literal["info", "pass", "fail", "warning"]
OverallStatus = Literal["PASS", "FAIL", "NEEDS_REVIEW"]
DataStatus = Literal["illustrative", "public_standard", "licensed"]
DataOrigin = Literal["illustrative", "public_standard", "licensed", "unattributed"]
DataVerificationStatus = Literal["VERIFIED", "NEEDS_REVIEW"]
SignStatus = Literal["UNSIGNED_ADVISORY", "SIGNED"]
Governing = Literal["overload_coordination", "voltage_drop", "short_circuit"]
StepId = Literal["current", "protection", "ampacity", "voltage_drop", "short_circuit", "summary"]
CheckName = Literal["overload_coordination", "voltage_drop", "short_circuit"]
CitationKey = Literal[
    "IB_load", "In_selection", "coord_overload", "voltage_drop", "sc_adiabatic", "install_method"
]


# --- traceability (docs/03 §3.2) ---
class Citation(BaseModel):
    """Human-readable norm reference, optionally deep-linked into the norm library (docs/20 §9).

    `doc_id`/`anchor` turn «ПУЭ РК, п. 40» into a link that opens the actual clause text. They
    are OPTIONAL and backward-compatible: a citation without them renders exactly as before.
    """

    standard: str
    clause: Optional[str] = None
    table: Optional[str] = None
    edition: Optional[str] = None
    note: Optional[str] = None
    doc_id: Optional[str] = None  # 'V1500010851'
    anchor: Optional[str] = None  # 'z1204'

    @model_serializer(mode="wrap")
    def _omit_unresolved_links(
        self, handler: Callable[["Citation"], dict[str, Any]]
    ) -> dict[str, Any]:
        """Drop `doc_id`/`anchor` when unset, so adding them changes no existing bytes.

        A blanket `exclude_none=True` would satisfy docs/20 §9's wording but violate §12.5:
        today's exported JSON carries `"clause": null` / `"edition": null` / `"note": null`, and
        dropping those too would change every golden report and export byte-for-byte. Only the
        two NEW fields are omitted while unresolved.
        """
        data = handler(self)
        for key in ("doc_id", "anchor"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


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
    trip_curve_type: Literal["B", "C", "D"] = "C"  # MCB magnetic band (Studio TCC, docs/10)


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
    source_document: Optional[str] = None


class DataSourceRecord(BaseModel):
    """Provenance declared by a pack for one numeric data family (docs/04 §4.6)."""

    origin: DataOrigin
    source_document: Optional[str] = None
    source_url: Optional[str] = None
    entered_by: Optional[str] = None
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    note: Optional[str] = None


class DataSectionAssessment(BaseModel):
    section: str
    source: DataSourceRecord
    trusted: bool
    issues: list[str] = Field(default_factory=list)


class DataProvenanceSummary(BaseModel):
    pack_name: str
    pack_version: str
    pack_origin: DataStatus
    verification_status: DataVerificationStatus
    used_sections: list[DataSectionAssessment]
    untrusted_sections: list[str]


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
    data_provenance: DataProvenanceSummary
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
def provenance_note_for(summary: DataProvenanceSummary) -> str:
    """Human-readable projection of deterministic per-section trust (docs/04 §4.6)."""
    identity = (
        f"Норм-пакет {summary.pack_name} v{summary.pack_version}; "
        f"происхождение: {summary.pack_origin}."
    )
    if summary.verification_status == "VERIFIED":
        return f"{identity} Все использованные числовые секции атрибутированы и верифицированы."
    sections = ", ".join(summary.untrusted_sections) or "не определены"
    origins = {assessment.source.origin for assessment in summary.used_sections}
    origin_warnings: list[str] = []
    if "illustrative" in origins:
        origin_warnings.append("Среди использованных секций есть СИНТЕТИЧЕСКИЕ данные.")
    if "unattributed" in origins:
        origin_warnings.append("Среди использованных секций есть неатрибутированные данные.")
    origin_warning = " ".join(origin_warnings)
    if origin_warning:
        origin_warning += " "
    return (
        f"{identity} Данные требуют проверки (NEEDS_REVIEW). Недоверенные секции: {sections}. "
        f"{origin_warning}PASS/FAIL отражает только детерминированную арифметику и не "
        "является заявлением о "
        "полном соответствии стандарту."
    )
