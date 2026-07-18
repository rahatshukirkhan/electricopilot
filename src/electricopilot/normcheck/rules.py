"""Pure R01-R10 rule registry. Thresholds come only from DataPack.normcheck."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Literal

from .models import BoardContext, Finding, FindingScope, RuleSource, Severity

RuleFunction = Callable[[BoardContext, RuleSource], list[Finding]]


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    severity: Severity
    scope: FindingScope
    title: str
    function: RuleFunction
    required_data_sections: tuple[str, ...]


RULES: dict[str, RuleSpec] = {}


def rule(
    rule_id: str, severity: Severity, scope: FindingScope, title: str, *,
    required_data_sections: tuple[str, ...] = (),
) -> Callable[[RuleFunction], RuleFunction]:
    def decorate(fn: RuleFunction) -> RuleFunction:
        if rule_id in RULES:
            raise RuntimeError(f"duplicate normcheck rule {rule_id}")
        RULES[rule_id] = RuleSpec(
            rule_id, severity, scope, title, fn, required_data_sections,
        )
        return fn
    return decorate


def _finding(
    spec: RuleSpec,
    source: RuleSource,
    *,
    detail: str,
    observed: dict[str, Any],
    required: dict[str, Any],
    circuit_id: str | None = None,
    circuit_ref: str | None = None,
    status: Literal["violation", "not_checked"] = "violation",
    reason: str | None = None,
) -> Finding:
    return Finding(
        rule_id=spec.rule_id,
        severity=spec.severity,
        status=status,
        scope=spec.scope,
        circuit_id=circuit_id,
        circuit_ref=circuit_ref,
        title=spec.title,
        detail=detail,
        observed=observed,
        required=required,
        citation=source.citation,
        source_section=source.section,
        source_trusted=source.trusted,
        reason=reason,
    )


def _spec(rule_id: str) -> RuleSpec:
    return RULES[rule_id]


_SIZING_SECTIONS = (
    "standard_ratings",
    "standard_sections",
    "device_parameters",
    "overload_rule",
    "ampacity",
    "ambient_correction",
    "grouping_correction",
    "adiabatic_k",
    "resistivity",
    "reactance",
)


@rule(
    "R01", "error", "circuit", "Расчёт цепи завершился FAIL",
    required_data_sections=_SIZING_SECTIONS,
)
def r01_failed_circuit(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R01")
    findings: list[Finding] = []
    for circuit in ctx.circuits:
        if circuit.result.overall_status != "FAIL":
            continue
        findings.append(_finding(
            spec, source,
            circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
            detail=(
                "Числовое ядро не нашло сечение, проходящее все активные проверки; "
                f"определяющий критерий: {circuit.result.selected_cable.governing_constraint}."
            ),
            observed={
                "overall_status": circuit.result.overall_status,
                "governing": circuit.result.selected_cable.governing_constraint,
            },
            required={"overall_status": "PASS"},
        ))
    return findings


@rule("R02", "error", "circuit", "Розеточная цепь без требуемого УЗО")
def r02_socket_rcd(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R02")
    max_ma = float(source.config["max_rcd_ma"])
    findings: list[Finding] = []
    for circuit in ctx.circuits:
        if circuit.request.load.purpose != "socket":
            continue
        rcd = (circuit.project_circuit.get("meta") or {}).get("rcd") or {}
        present = bool(rcd.get("present"))
        try:
            ma = float(rcd["ma"]) if present and rcd.get("ma") is not None else None
        except (TypeError, ValueError):
            findings.append(_finding(
                spec, source,
                circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
                detail="Уставка УЗО имеет неверный формат; нормативный вывод невозможен.",
                observed={"rcd_present": present, "rcd_ma": rcd.get("ma")},
                required={"max_rcd_ma": max_ma},
                status="not_checked", reason="invalid_input",
            ))
            continue
        if present and ma is not None and ma <= 0:
            findings.append(_finding(
                spec, source,
                circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
                detail="Уставка УЗО должна быть положительным числом.",
                observed={"rcd_present": present, "rcd_ma": ma},
                required={"max_rcd_ma": max_ma},
                status="not_checked", reason="invalid_input",
            ))
            continue
        if present and ma is not None and ma <= max_ma:
            continue
        findings.append(_finding(
            spec, source,
            circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
            detail="Для розеточной цепи не задано УЗО с уставкой в пределах порога пака.",
            observed={"rcd_present": present, "rcd_ma": ma},
            required={"rcd_present": True, "max_rcd_ma": max_ma},
        ))
    return findings


def _feeder_drop_pct(ctx: BoardContext) -> tuple[float | None, list[str]]:
    feeder = (ctx.project.get("supply") or {}).get("feeder") or {}
    missing = [key for key in ("length_m", "section_mm2", "material") if feeder.get(key) is None]
    if missing:
        return None, missing
    supply = ctx.project.get("supply") or {}
    try:
        voltage_v = float(supply.get("voltage_v") or 0)
        length_m = float(feeder["length_m"])
        section_mm2 = float(feeder["section_mm2"])
    except (TypeError, ValueError):
        return None, ["supply.feeder numeric fields invalid"]
    if length_m <= 0 or section_mm2 <= 0:
        return None, ["supply.feeder length_m/section_mm2 must be > 0"]
    phase = ctx.report["board"]["totals"]["phase"]
    current_a = max(float(phase[name]["A"]) for name in ("L1", "L2", "L3"))
    if voltage_v <= 0:
        return None, ["supply.voltage_v"]
    material = str(feeder["material"])
    if material not in ("Cu", "Al"):
        return None, ["supply.feeder.material invalid"]
    rho = ctx.pack.resistivity(material)  # type: ignore[arg-type]
    resistance = rho * length_m / section_mm2
    return math.sqrt(3) * current_a * resistance / voltage_v * 100, []


@rule(
    "R03", "error", "circuit", "Суммарное падение напряжения выше лимита",
    required_data_sections=_SIZING_SECTIONS,
)
def r03_cumulative_voltage_drop(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R03")
    feeder_pct, missing = _feeder_drop_pct(ctx)
    if feeder_pct is None:
        return [_finding(
            spec, source,
            detail="Проверка невозможна: не задана полная модель питающей линии.",
            observed={"missing_fields": missing}, required={"supply.feeder": [
                "length_m", "section_mm2", "material",
            ]}, status="not_checked", reason="missing_input",
        )]
    limits = source.config["max_total_vd_pct_by_purpose"]
    findings: list[Finding] = []
    for circuit in ctx.circuits:
        purpose = circuit.request.load.purpose
        if purpose not in limits:
            findings.append(_finding(
                spec, source,
                circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
                detail=f"В паке отсутствует лимит суммарного ΔU для purpose={purpose}.",
                observed={"purpose": purpose}, required={"configured_limit": True},
                status="not_checked", reason="missing_rule_value",
            ))
            continue
        total_pct = feeder_pct + circuit.result.voltage_drop_pct
        limit_pct = float(limits[purpose])
        if total_pct <= limit_pct:
            continue
        findings.append(_finding(
            spec, source,
            circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
            detail="Падение на фидере и отходящей цепи суммарно превышает лимит пака.",
            observed={
                "feeder_vd_pct": round(feeder_pct, 3),
                "circuit_vd_pct": circuit.result.voltage_drop_pct,
                "total_vd_pct": round(total_pct, 3),
            },
            required={"max_total_vd_pct": limit_pct},
        ))
    return findings


@rule(
    "R04", "warning", "circuit", "Номинальная селективность ввода не обеспечена",
    required_data_sections=("standard_ratings", "device_parameters"),
)
def r04_incomer_nominal(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R04")
    incomer = (ctx.project.get("supply") or {}).get("incomer") or {}
    if incomer.get("In_a") is None:
        return [_finding(
            spec, source,
            detail="Проверка невозможна: не задан номинал вводного аппарата.",
            observed={"incomer_In_a": None}, required={"supply.incomer.In_a": "required"},
            status="not_checked", reason="missing_input",
        )]
    try:
        incomer_in = float(incomer["In_a"])
    except (TypeError, ValueError):
        return [_finding(
            spec, source,
            detail="Проверка невозможна: номинал вводного аппарата имеет неверный формат.",
            observed={"incomer_In_a": incomer["In_a"]},
            required={"supply.incomer.In_a": "positive number"},
            status="not_checked", reason="invalid_input",
        )]
    if incomer_in <= 0:
        return [_finding(
            spec, source,
            detail="Проверка невозможна: номинал вводного аппарата должен быть положительным.",
            observed={"incomer_In_a": incomer_in},
            required={"supply.incomer.In_a": "positive number"},
            status="not_checked", reason="invalid_input",
        )]
    findings: list[Finding] = []
    for circuit in ctx.circuits:
        outgoing = circuit.result.selected_protection.In_a
        if incomer_in > outgoing:
            continue
        findings.append(_finding(
            spec, source,
            circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
            detail="Номинал вводного аппарата не больше номинала отходящего аппарата.",
            observed={"incomer_In_a": incomer_in, "outgoing_In_a": outgoing},
            required={"incomer_In_a_gt_outgoing_In_a": True},
        ))
    return findings


@rule("R05", "warning", "board", "Перекос фаз выше порога")
def r05_phase_imbalance(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R05")
    observed = float(ctx.report["board"]["totals"]["imbalance_pct"])
    required = float(source.config["max_imbalance_pct"])
    if observed <= required:
        return []
    return [_finding(
        spec, source,
        detail="Расчётный перекос фаз превышает порог normcheck-пака.",
        observed={"imbalance_pct": observed}, required={"max_imbalance_pct": required},
    )]


@rule("R06", "warning", "board", "Резерв мест щита ниже порога")
def r06_board_spare(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R06")
    observed = float(ctx.report["board"]["ways"]["spare_pct"])
    required = float(source.config["min_spare_pct"])
    if observed >= required:
        return []
    return [_finding(
        spec, source,
        detail="Доля свободных мест щита ниже минимального резерва из пака.",
        observed={"spare_pct": observed}, required={"min_spare_pct": required},
    )]


@rule("R07", "warning", "circuit", "Кривая аппарата нежелательна для двигателя")
def r07_motor_curve(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R07")
    disallowed = {str(value) for value in source.config["motor_disallowed_curves"]}
    findings: list[Finding] = []
    for circuit in ctx.circuits:
        curve = circuit.request.protection.trip_curve_type
        if circuit.request.load.purpose != "motor" or curve not in disallowed:
            continue
        findings.append(_finding(
            spec, source,
            circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
            detail="Выбранная кривая входит в запрещённый для motor список пака.",
            observed={"purpose": "motor", "trip_curve_type": curve},
            required={"trip_curve_type_not_in": sorted(disallowed)},
        ))
    return findings


@rule(
    "R08", "error", "circuit", "Сечение алюминиевого проводника ниже минимума",
    required_data_sections=_SIZING_SECTIONS,
)
def r08_aluminium_minimum(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R08")
    minimum = float(source.config["min_al_section_mm2"])
    findings: list[Finding] = []
    for circuit in ctx.circuits:
        cable = circuit.result.selected_cable
        if cable.material != "Al" or cable.cross_section_mm2 >= minimum:
            continue
        findings.append(_finding(
            spec, source,
            circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
            detail="Рассчитанное сечение Al ниже минимального значения из пака.",
            observed={"material": cable.material, "section_mm2": cable.cross_section_mm2},
            required={"min_al_section_mm2": minimum},
        ))
    return findings


@rule("R09", "warning", "circuit", "Проверка короткого замыкания пропущена")
def r09_missing_iscc(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R09")
    findings: list[Finding] = []
    for circuit in ctx.circuits:
        iscc = circuit.request.protection.prospective_fault_current_a
        if iscc is not None:
            continue
        findings.append(_finding(
            spec, source,
            circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
            detail="I_scc не задан; адиабатическая проверка числового ядра была пропущена.",
            observed={"prospective_fault_current_a": None},
            required={"prospective_fault_current_a": "required"},
        ))
    return findings


def _required_pe(section_mm2: float, table: list[dict[str, Any]]) -> float | None:
    for row in table:
        maximum = row.get("phase_max_mm2")
        if maximum is not None and section_mm2 > float(maximum):
            continue
        if row.get("same_as_phase"):
            return section_mm2
        if row.get("fixed_mm2") is not None:
            return float(row["fixed_mm2"])
        if row.get("factor") is not None:
            return section_mm2 * float(row["factor"])
        return None
    return None


@rule(
    "R10", "info", "circuit", "Сечение PE не соответствует таблице пака",
    required_data_sections=_SIZING_SECTIONS,
)
def r10_pe_section(ctx: BoardContext, source: RuleSource) -> list[Finding]:
    spec = _spec("R10")
    table = list(source.config["pe_section_table"])
    findings: list[Finding] = []
    for circuit in ctx.circuits:
        phase_section = circuit.result.selected_cable.cross_section_mm2
        required_pe = _required_pe(phase_section, table)
        meta = circuit.project_circuit.get("meta") or {}
        observed_pe = meta.get("pe_section_mm2")
        if required_pe is None:
            findings.append(_finding(
                spec, source,
                circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
                detail="Таблица PE пака не покрывает рассчитанное фазное сечение.",
                observed={"phase_section_mm2": phase_section},
                required={"pe_section_table_match": True},
                status="not_checked", reason="missing_rule_value",
            ))
            continue
        if observed_pe is None:
            findings.append(_finding(
                spec, source,
                circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
                detail="В проекте не задано сечение PE для сравнения с таблицей пака.",
                observed={"pe_section_mm2": None, "phase_section_mm2": phase_section},
                required={"pe_section_mm2": required_pe},
                status="not_checked", reason="missing_input",
            ))
            continue
        try:
            observed_pe_value = float(observed_pe)
        except (TypeError, ValueError):
            findings.append(_finding(
                spec, source,
                circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
                detail="Сечение PE имеет неверный формат.",
                observed={"pe_section_mm2": observed_pe},
                required={"pe_section_mm2": required_pe},
                status="not_checked", reason="invalid_input",
            ))
            continue
        if observed_pe_value >= required_pe:
            continue
        findings.append(_finding(
            spec, source,
            circuit_id=circuit.circuit_id, circuit_ref=circuit.ref,
            detail="Заданное сечение PE ниже требуемого по таблице пака.",
            observed={"pe_section_mm2": observed_pe_value, "phase_section_mm2": phase_section},
            required={"min_pe_section_mm2": required_pe},
        ))
    return findings
