"""Typed contracts and immutable context for deterministic board normcheck (docs/14)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..data.loader import DataPack
from ..models import Citation, LlmNarrative, SizingRequest, SizingResult

Severity = Literal["error", "warning", "info"]
FindingStatus = Literal["violation", "not_checked"]
FindingScope = Literal["circuit", "board"]


class Finding(BaseModel):
    rule_id: str
    severity: Severity
    status: FindingStatus
    scope: FindingScope
    circuit_id: str | None = None
    circuit_ref: str | None = None
    title: str
    detail: str
    observed: dict[str, Any] = Field(default_factory=dict)
    required: dict[str, Any] = Field(default_factory=dict)
    citation: Citation | None = None
    source_section: str
    data_sections: list[str] = Field(default_factory=list)
    source_trusted: bool
    reason: str | None = None


class NormcheckSummary(BaseModel):
    errors: int = 0
    warnings: int = 0
    infos: int = 0
    not_checked: int = 0
    total: int = 0


class NormcheckReport(BaseModel):
    findings: list[Finding]
    summary: NormcheckSummary
    norm_pack: dict[str, str]
    data_provenance: dict[str, Any]
    data_identity: str
    provenance_note: str
    disclaimer: str
    signoff_notice: str
    narrative: LlmNarrative | None = None


@dataclass(frozen=True)
class CircuitContext:
    project_circuit: dict[str, Any]
    request: SizingRequest
    result: SizingResult
    row: dict[str, Any]

    @property
    def circuit_id(self) -> str:
        return str(self.project_circuit.get("id") or "")

    @property
    def ref(self) -> str:
        return str(self.project_circuit.get("ref") or "")


@dataclass(frozen=True)
class BoardContext:
    project: dict[str, Any]
    pack: DataPack
    report: dict[str, Any]
    circuits: tuple[CircuitContext, ...]


@dataclass(frozen=True)
class RuleSource:
    section: str
    config: dict[str, Any]
    citation: Citation
    data_sections: tuple[str, ...]
    trusted: bool
    issues: tuple[str, ...]
