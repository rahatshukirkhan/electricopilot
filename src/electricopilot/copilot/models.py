"""Typed Copilot request, operation, proposal, and response contracts."""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ..models import SizingRequest
from ..project_contract import CircuitMeta, Project

# Floor plans travel as data URLs so the whole request stays one JSON body under the
# global max_request_bytes bound (docs/19). Raster images become OpenRouter image_url
# parts; PDF becomes a file part (Gemini reads both natively). Anything else (svg, html,
# office formats) is rejected before the LLM.
_ATTACHMENT_DATA_URL = re.compile(
    r"^data:(?:image/(?:png|jpeg|webp)|application/pdf);base64,[A-Za-z0-9+/]+=*$"
)
_MAX_ATTACHMENT_CHARS = 1_500_000  # ~1.1 MB decoded; client downscales photos before upload


class _ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HistoryMessage(_ClosedModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class CopilotRequest(_ClosedModel):
    project: Project
    message: str = Field(min_length=1, max_length=8000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=20)
    attachments: list[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def _attachments_are_bounded_plan_data_urls(self) -> "CopilotRequest":
        for url in self.attachments:
            if len(url) > _MAX_ATTACHMENT_CHARS:
                raise ValueError(
                    "вложение слишком большое — уменьшите фото или сожмите PDF планировки"
                )
            if not _ATTACHMENT_DATA_URL.match(url):
                raise ValueError(
                    "вложение должно быть data-URL формата PNG, JPEG, WebP или PDF"
                )
        return self


class AddOperation(_ClosedModel):
    op: Literal["add"]
    circuit_id: str | None = Field(default=None, min_length=1)
    request: SizingRequest
    meta: CircuitMeta = Field(default_factory=CircuitMeta)
    ref: str = ""

    @model_validator(mode="after")
    def _no_forged_import_evidence(self) -> "AddOperation":
        if self.meta.import_declaration is not None or self.meta.pe_section_mm2 is not None:
            raise ValueError("Copilot cannot create import provenance or PE engineering values")
        return self


class EditOperation(_ClosedModel):
    op: Literal["edit"]
    circuit_id: str = Field(min_length=1)
    request: SizingRequest | None = None
    meta: CircuitMeta | None = None
    ref: str | None = None

    @model_validator(mode="after")
    def _has_change(self) -> "EditOperation":
        if self.request is None and self.meta is None and self.ref is None:
            raise ValueError("edit requires at least one of request, meta, or ref")
        if self.meta is not None and (
            self.meta.import_declaration is not None or self.meta.pe_section_mm2 is not None
        ):
            raise ValueError("Copilot cannot create import provenance or PE engineering values")
        return self


class DeleteOperation(_ClosedModel):
    op: Literal["delete"]
    circuit_id: str = Field(min_length=1)


ProposalOperation = Annotated[
    AddOperation | EditOperation | DeleteOperation,
    Field(discriminator="op"),
]
OPERATIONS_ADAPTER = TypeAdapter(list[ProposalOperation])


class CircuitMetrics(_ClosedModel):
    section_mm2: float
    protection_in_a: float
    design_current_a: float
    voltage_drop_pct: float
    status: str


class CircuitDiff(_ClosedModel):
    circuit_id: str
    ref: str
    change: Literal["add", "edit", "delete"]
    before: CircuitMetrics | None
    after: CircuitMetrics | None


class BoardMetrics(_ClosedModel):
    status: str
    connected_kw: float
    imbalance_pct: float | None
    incomer_md_a: float


class ProposalDiff(_ClosedModel):
    circuits: list[CircuitDiff]
    board_before: BoardMetrics
    board_after: BoardMetrics
    data_identity: str
    data_provenance: dict[str, Any]
    disclaimer: str
    signoff_notice: str


class Proposal(_ClosedModel):
    ops: list[ProposalOperation]
    diff: ProposalDiff


class CopilotResponse(_ClosedModel):
    ok: bool
    reply: str
    proposal: Proposal | None = None
    model: str
    provenance_ok: bool = True
    unverified_numbers: list[str] = Field(default_factory=list)
    incomplete: bool = False
    error: str | None = None
