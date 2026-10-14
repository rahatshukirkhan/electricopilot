"""Deterministic Copilot tools and in-memory proposal application."""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Callable

from pydantic import ValidationError

from ..data.loader import DataPack
from ..engine import size
from ..exceptions import ProjectContractError, ProjectTopologyError
from ..models import SizingRequest
from ..normcheck import build_normcheck_report
from ..project import build_project_report
from ..project_contract import Project, validate_project
from ..topology import validate_project_topology
from .models import (
    AddOperation,
    BoardMetrics,
    CircuitDiff,
    CircuitMetrics,
    DeleteOperation,
    EditOperation,
    OPERATIONS_ADAPTER,
    Proposal,
    ProposalDiff,
    ProposalOperation,
)


def _single_argument_schema(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    nested = deepcopy(schema)
    definitions = nested.pop("$defs", {})
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {name: nested},
        "required": [name],
        "additionalProperties": False,
    }
    if definitions:
        parameters["$defs"] = definitions
    return parameters


_REQUEST_ARGUMENT = _single_argument_schema("request", SizingRequest.model_json_schema())
_OPS_ARGUMENT = _single_argument_schema("ops", OPERATIONS_ADAPTER.json_schema())

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_board_state",
            "description": "Fresh deterministic board report and canonical circuit identifiers.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parse_circuit",
            "description": "Parse one natural-language circuit description into SizingRequest.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "minLength": 1}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute",
            "description": "Run the deterministic sizing core for a typed request.",
            "parameters": _REQUEST_ARGUMENT,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_normcheck",
            "description": "Run fresh deterministic R01-R10 board checks.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_changes",
            "description": "Validate add/edit/delete operations and build a deterministic before/after diff.",
            "parameters": _OPS_ARGUMENT,
        },
    },
]


class CopilotToolError(ValueError):
    """Stable tool/proposal failure surfaced without applying partial changes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Field-name -> electrician-facing Russian label, used only to humanize pydantic ValidationErrors
# below (docs review: raw `str(ValidationError)` dumps — "7 validation errors for SizingRequest",
# dotted Python field paths, https://errors.pydantic.dev links — must never reach the Copilot chat).
_FIELD_LABELS_RU: dict[str, str] = {
    "method": "метод прокладки", "material": "материал жилы", "insulation": "изоляция",
    "ambient_temp_c": "температура окружающей среды", "grouping_circuits": "группировка цепей",
    "length_m": "длина кабеля", "device_class": "класс аппарата", "trip_curve_type": "характеристика расцепителя",
    "voltage_v": "напряжение", "phases": "число фаз", "power_factor": "cosφ", "purpose": "назначение",
    "power_w": "мощность (Вт)", "current_a": "ток (А)", "description": "описание цепи",
    "prospective_fault_current_a": "ток КЗ", "disconnection_time_s": "время отключения",
    "max_voltage_drop_pct": "предел ΔU", "circuit_id": "идентификатор цепи", "ref": "обозначение цепи",
}


def _humanize_validation_error(exc: ValidationError, *, limit: int = 4) -> str:
    """Turn a pydantic ValidationError into one short Russian sentence an electrician can act on —
    no dotted field paths, no per-error boilerplate, no errors.pydantic.dev links."""
    parts: list[str] = []
    seen: set[str] = set()
    errors = exc.errors()
    for err in errors:
        loc = [str(p) for p in err.get("loc", ()) if not isinstance(p, int)]
        field = loc[-1] if loc else ""
        label = _FIELD_LABELS_RU.get(field, field or "параметр")
        if label in seen:
            continue
        seen.add(label)
        kind = err.get("type", "")
        if kind == "missing":
            parts.append(f"не указан(о) «{label}»")
        elif kind == "literal_error":
            allowed = err.get("ctx", {}).get("expected", "")
            parts.append(f"«{label}»: недопустимое значение" + (f" (ожидается {allowed})" if allowed else ""))
        else:
            parts.append(f"«{label}»: значение не проходит проверку")
        if len(parts) >= limit:
            break
    extra = len(errors) - len(parts)
    tail = f" и ещё {extra} парам." if extra > 0 else ""
    joined = "; ".join(parts) if parts else "переданные параметры не проходят проверку"
    return f"Не удалось применить: уточни {joined}{tail}."


def _next_id(used: set[str]) -> str:
    index = 1
    while f"copilot-{index}" in used:
        index += 1
    return f"copilot-{index}"


def _normalize_operations(raw_ops: Any, project: Project) -> list[ProposalOperation]:
    try:
        operations = OPERATIONS_ADAPTER.validate_python(raw_ops)
    except ValidationError as exc:
        raise CopilotToolError("invalid_proposal", _humanize_validation_error(exc)) from None
    if not operations:
        raise CopilotToolError("invalid_proposal", "proposal requires at least one operation")
    if len(operations) > project.supply.ways_total * 2:
        raise CopilotToolError("invalid_proposal", "proposal contains too many operations")

    used = {circuit.id for circuit in project.circuits}
    targeted: set[str] = set()
    normalized: list[ProposalOperation] = []
    for operation in operations:
        if isinstance(operation, AddOperation):
            circuit_id = operation.circuit_id or _next_id(used)
            if circuit_id in used:
                raise CopilotToolError("invalid_proposal", f"duplicate circuit id: {circuit_id}")
            if circuit_id in targeted:
                raise CopilotToolError("invalid_proposal", f"repeated circuit target: {circuit_id}")
            used.add(circuit_id)
            targeted.add(circuit_id)
            normalized.append(operation.model_copy(update={"circuit_id": circuit_id}))
            continue
        if operation.circuit_id not in used:
            raise CopilotToolError(
                "invalid_proposal", f"unknown circuit id: {operation.circuit_id}",
            )
        if operation.circuit_id in targeted:
            raise CopilotToolError(
                "invalid_proposal", f"repeated circuit target: {operation.circuit_id}",
            )
        targeted.add(operation.circuit_id)
        if isinstance(operation, DeleteOperation):
            used.remove(operation.circuit_id)
        normalized.append(operation)
    return normalized


def _apply_operations(project: Project, operations: list[ProposalOperation]) -> Project:
    payload = project.model_dump(mode="json")
    circuits = deepcopy(payload["circuits"])
    for operation in operations:
        if isinstance(operation, AddOperation):
            circuits.append({
                "id": operation.circuit_id,
                "ref": operation.ref,
                "sort_index": len(circuits),
                "request": operation.request.model_dump(mode="json"),
                "meta": operation.meta.model_dump(mode="json"),
                "result": None,
                "signoff": {"status": "UNSIGNED_ADVISORY"},
            })
        elif isinstance(operation, EditOperation):
            index = next(i for i, item in enumerate(circuits) if item["id"] == operation.circuit_id)
            changed = deepcopy(circuits[index])
            if operation.request is not None:
                changed["request"] = operation.request.model_dump(mode="json")
            if operation.meta is not None:
                import_evidence = changed.get("meta", {}).get("import_declaration")
                pe_section = changed.get("meta", {}).get("pe_section_mm2")
                changed["meta"] = operation.meta.model_dump(mode="json")
                changed["meta"]["import_declaration"] = import_evidence
                changed["meta"]["pe_section_mm2"] = pe_section
            if operation.ref is not None:
                changed["ref"] = operation.ref
            changed["result"] = None
            changed["signoff"] = {"status": "UNSIGNED_ADVISORY"}
            circuits[index] = changed
        else:
            circuits = [item for item in circuits if item["id"] != operation.circuit_id]
        for index, circuit in enumerate(circuits):
            circuit["sort_index"] = index

    if len(circuits) > project.supply.ways_total:
        raise CopilotToolError("invalid_proposal", "proposal exceeds supply.ways_total")
    payload["circuits"] = circuits
    try:
        candidate = validate_project(payload)
        validate_project_topology(candidate.model_dump(mode="json"))
    except ValidationError as exc:
        raise CopilotToolError("invalid_proposal", _humanize_validation_error(exc)) from None
    except (ProjectContractError, ProjectTopologyError, ValueError) as exc:
        raise CopilotToolError("invalid_proposal", str(exc)) from None
    return candidate


def _row_metrics(row: dict[str, Any]) -> CircuitMetrics:
    spec = row["spec"]
    return CircuitMetrics(
        section_mm2=float(spec["section_mm2"]),
        protection_in_a=float(spec["In_a"]),
        design_current_a=float(row["IB_a"]),
        voltage_drop_pct=float(row["dU_pct"]),
        status=str(row["status"]),
    )


def _board_metrics(report: dict[str, Any]) -> BoardMetrics:
    board = report["board"]
    totals = board["totals"]
    return BoardMetrics(
        status=str(board["status"]),
        connected_kw=float(totals["connected_kw"]),
        imbalance_pct=(
            float(totals["imbalance_pct"]) if totals["imbalance_pct"] is not None else None
        ),
        incomer_md_a=float(board["demand"]["incomer_md_a"]),
    )


def build_proposal(project: Project, raw_ops: Any, pack: DataPack) -> tuple[Proposal, Project, dict[str, Any], dict[str, Any]]:
    """Apply normalized operations only in memory and compute a stable fresh diff."""
    operations = _normalize_operations(raw_ops, project)
    candidate = _apply_operations(project, operations)
    before = build_project_report(project, data_pack=pack)
    after = build_project_report(candidate, data_pack=pack)
    before_rows = {str(row["id"]): row for row in before["rows"]}
    after_rows = {str(row["id"]): row for row in after["rows"]}
    circuit_diffs: list[CircuitDiff] = []
    for operation in operations:
        circuit_id = str(operation.circuit_id)
        old = before_rows.get(circuit_id)
        new = after_rows.get(circuit_id)
        circuit_diffs.append(CircuitDiff(
            circuit_id=circuit_id,
            ref=str((new or old or {}).get("ref") or ""),
            change=operation.op,
            before=_row_metrics(old) if old is not None else None,
            after=_row_metrics(new) if new is not None else None,
        ))
    proposal = Proposal(
        ops=operations,
        diff=ProposalDiff(
            circuits=circuit_diffs,
            board_before=_board_metrics(before),
            board_after=_board_metrics(after),
            data_identity=str(after["data_identity"]),
            data_provenance=after["data_provenance"],
            disclaimer=str(after["disclaimer"]),
            signoff_notice=str(after["signoff_notice"]),
        ),
    )
    return proposal, candidate, before, after


def report_tool_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "board": report["board"],
        "rows": report["rows"],
        "data_identity": report["data_identity"],
        "data_provenance": report["data_provenance"],
        "disclaimer": report["disclaimer"],
        "signoff_notice": report["signoff_notice"],
    }


def board_state_payload(project: Project, report: dict[str, Any]) -> dict[str, Any]:
    """Expose canonical editable inputs beside fresh results, never client snapshots/signoff."""
    payload = report_tool_payload(report)
    payload["project"] = {
        "id": project.id,
        "norm_pack": project.norm_pack,
        "supply": project.supply.model_dump(mode="json"),
        "circuits": [
            {
                "id": circuit.id,
                "ref": circuit.ref,
                "request": circuit.request.model_dump(mode="json"),
                "meta": circuit.meta.model_dump(
                    mode="json", exclude={"import_declaration", "pe_section_mm2"},
                ),
            }
            for circuit in project.circuits
        ],
    }
    return payload


_NUMBER = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:[ \u00a0\u2009\u202f]\d{3})+|\d+)"
    r"(?:[.,]\d+)?(?:[eE][-+]?\d+)?"
)
_RULE = re.compile(r"\bR\d{2}\b")
_CITATION = re.compile(r"(?:IEC|МЭК|ГОСТ|EN|BS|DIN)\s*\d[\d.\-]*|§\s*\d[\d.]*", re.I)


def _collect_numbers(value: Any, found: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        found.add(float(value))
    elif isinstance(value, dict):
        for child in value.values():
            _collect_numbers(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_numbers(child, found)


def check_reply_provenance(text: str, evidence: list[Any]) -> tuple[bool, list[str]]:
    """Allow only numbers present as typed leaves of deterministic tool evidence.

    A one-way \u00d71000 scale equivalence is accepted alongside the exact value: evidence
    stores power_w=3600 and rcd ma=30, while an electrician-facing reply naturally says
    \u00ab3,6 \u043a\u0412\u0442\u00bb or \u00ab0,03 \u0410\u00bb. Only reply-in-the-larger-unit is forgiven (token*1000 traces
    to a leaf); the \u00f71000 direction is deliberately NOT allowed \u2014 with the absolute
    0.05 tolerance floor it would let any ~N*1000 token ride on small leaves like
    power_factor=1.0. Arbitrary numbers remain flagged.
    """
    allowed: set[float] = set()
    _collect_numbers(evidence, allowed)
    cleaned = _CITATION.sub(" ", _RULE.sub(" ", text))
    unverified: list[str] = []

    def matches(value: float) -> bool:
        return any(abs(value - item) <= max(0.05, 0.01 * abs(item)) for item in allowed)

    for token in dict.fromkeys(_NUMBER.findall(cleaned)):
        value = float(
            token.replace(" ", "")
            .replace("\u00a0", "")
            .replace("\u2009", "")
            .replace("\u202f", "")
            .replace(",", ".")
        )
        if not (matches(value) or matches(value * 1000)):
            unverified.append(token)
    return not unverified, unverified


ParseCircuit = Callable[[str, float], SizingRequest]


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    project: Project,
    pack: DataPack,
    parse_circuit: ParseCircuit,
    remaining_seconds: float,
) -> tuple[dict[str, Any], Proposal | None, list[Any]]:
    """Execute one allowlisted tool; never write project state."""
    try:
        if name == "get_board_state":
            if arguments:
                raise CopilotToolError("invalid_tool", "get_board_state takes no arguments")
            payload = board_state_payload(
                project, build_project_report(project, data_pack=pack),
            )
            return payload, None, [payload]
        if name == "parse_circuit":
            if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
                raise CopilotToolError("invalid_tool", "parse_circuit requires text")
            request = parse_circuit(arguments["text"], remaining_seconds)
            payload = {"request": request.model_dump(mode="json")}
            return payload, None, [payload]
        if name == "compute":
            if set(arguments) != {"request"}:
                raise CopilotToolError("invalid_tool", "compute requires request")
            request = SizingRequest.model_validate(arguments["request"])
            payload = size(request, data_pack=pack).model_dump(mode="json")
            return payload, None, [payload]
        if name == "run_normcheck":
            if arguments:
                raise CopilotToolError("invalid_tool", "run_normcheck takes no arguments")
            payload = build_normcheck_report(project, pack).model_dump(mode="json")
            return payload, None, [payload]
        if name == "propose_changes":
            if set(arguments) != {"ops"}:
                raise CopilotToolError("invalid_proposal", "propose_changes requires ops")
            proposal, _candidate, before, after = build_proposal(project, arguments["ops"], pack)
            payload = proposal.model_dump(mode="json")
            return payload, proposal, [report_tool_payload(before), report_tool_payload(after), payload]
    except CopilotToolError:
        raise
    except ValidationError as exc:
        raise CopilotToolError("invalid_tool", _humanize_validation_error(exc)) from None
    except ValueError as exc:
        raise CopilotToolError("invalid_tool", str(exc)) from None
    raise CopilotToolError("invalid_tool", f"unknown tool: {name}")
