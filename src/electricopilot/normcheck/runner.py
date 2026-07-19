"""Deterministic normcheck runner and stable result projection (docs/14)."""
from __future__ import annotations

from ..calculation_manifest import build_calculation_manifest, manifest_data_identity
from ..data.loader import DataPack
from ..engine import size
from ..models import DISCLAIMER, SizingRequest, provenance_note_for
from ..project import build_project_report
from ..project_contract import ProjectInput, project_payload, validate_project
from .models import (
    BoardContext,
    CircuitContext,
    Finding,
    NormcheckReport,
    NormcheckSummary,
    RuleSource,
)
from .rules import RULES, RuleSpec

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _context(project: ProjectInput, pack: DataPack) -> BoardContext:
    canonical = validate_project(project)
    payload = project_payload(canonical)
    report = build_project_report(canonical, data_pack=pack)
    rows = {str(row.get("id") or ""): row for row in report["rows"]}
    circuits: list[CircuitContext] = []
    for raw in payload["circuits"]:
        request = SizingRequest.model_validate(raw["request"])
        result = size(request, data_pack=pack)
        circuit_id = str(raw.get("id") or "")
        circuits.append(CircuitContext(
            project_circuit=raw,
            request=request,
            result=result,
            row=rows[circuit_id],
        ))
    return BoardContext(project=payload, pack=pack, report=report, circuits=tuple(circuits))


def _source(pack: DataPack, spec: RuleSpec) -> RuleSource | None:
    resolved = pack.normcheck_rule(spec.rule_id)
    if resolved is None:
        return None
    config, citation, assessment = resolved
    dependency_assessment = pack.assess_provenance(list(spec.required_data_sections))
    dependency_issues = tuple(
        f"{section_assessment.section}: {issue}"
        for section_assessment in dependency_assessment.used_sections
        for issue in section_assessment.issues
    )
    dependency_sections = tuple(
        section_assessment.section
        for section_assessment in dependency_assessment.used_sections
    )
    return RuleSource(
        section=assessment.section,
        config=config,
        citation=citation,
        data_sections=dependency_sections,
        trusted=assessment.trusted and not dependency_issues,
        issues=tuple(assessment.issues) + dependency_issues,
    )


def _unavailable(spec: RuleSpec, reason: str, detail: str) -> Finding:
    return Finding(
        rule_id=spec.rule_id,
        severity=spec.severity,
        status="not_checked",
        scope=spec.scope,
        title=f"Не проверено: {spec.title}",
        detail=detail,
        observed={"rule_configured": reason != "rule_not_configured"},
        required={"trusted_rule_source": True},
        citation=None,
        source_section=f"normcheck.{spec.rule_id}",
        data_sections=[],
        source_trusted=False,
        reason=reason,
    )


def _downgrade_untrusted(findings: list[Finding], spec: RuleSpec, source: RuleSource) -> list[Finding]:
    issues = ", ".join(source.issues) or "source is not trusted"
    if not findings:
        return [_unavailable(
            spec,
            reason="untrusted_source",
            detail=f"Правило не даёт нормативный PASS: {source.section}: {issues}.",
        ).model_copy(update={
            "citation": source.citation,
            "data_sections": list(source.data_sections),
        })]
    downgraded: list[Finding] = []
    for finding in findings:
        downgraded.append(finding.model_copy(update={
            "status": "not_checked",
            "title": f"Не проверено: {spec.title}",
            "detail": f"{finding.detail} Источник правила недоверенный: {issues}.",
            "source_trusted": False,
            "reason": finding.reason if finding.status == "not_checked" else "untrusted_source",
        }))
    return downgraded


def _sort_key(finding: Finding) -> tuple[int, str, str, str]:
    return (
        _SEVERITY_ORDER[finding.severity],
        finding.circuit_ref or "",
        finding.rule_id,
        finding.status,
    )


def run_normcheck(project: ProjectInput, pack: DataPack) -> list[Finding]:
    """Run R01-R10 over fresh server-side calculations and return stable findings."""
    ctx = _context(project, pack)
    findings: list[Finding] = []
    for rule_id in sorted(RULES):
        spec = RULES[rule_id]
        source = _source(pack, spec)
        if source is None:
            findings.append(_unavailable(
                spec, "rule_not_configured",
                f"В norm-паке {pack.meta.name} нет конфигурации {rule_id}.",
            ))
            continue
        produced = spec.function(ctx, source)
        findings.extend(produced if source.trusted else _downgrade_untrusted(produced, spec, source))
    return sorted(findings, key=_sort_key)


def summarize_findings(findings: list[Finding]) -> NormcheckSummary:
    summary = NormcheckSummary(total=len(findings))
    values = summary.model_dump()
    for finding in findings:
        if finding.status == "not_checked":
            values["not_checked"] += 1
        else:
            values[{"error": "errors", "warning": "warnings", "info": "infos"}[finding.severity]] += 1
    return NormcheckSummary.model_validate(values)


def build_normcheck_report(project: ProjectInput, pack: DataPack) -> NormcheckReport:
    canonical = validate_project(project)
    findings = run_normcheck(canonical, pack)
    provenance = pack.assess_provenance(
        sorted({
            section
            for finding in findings
            for section in (finding.source_section, *finding.data_sections)
        })
    )
    manifest = build_calculation_manifest(canonical, pack)
    data_identity = (
        f"{manifest_data_identity(manifest)} "
        f"Проверка использованных данных: {provenance.verification_status}."
    )
    return NormcheckReport(
        findings=findings,
        summary=summarize_findings(findings),
        norm_pack={
            "name": pack.meta.name,
            "version": pack.meta.version,
            "status": pack.meta.status,
            "verification_status": provenance.verification_status,
        },
        data_provenance=provenance.model_dump(),
        data_identity=data_identity,
        calculation_id=manifest.calculation_id,
        calculation_manifest=manifest,
        provenance_note=provenance_note_for(provenance),
        disclaimer=DISCLAIMER,
        signoff_notice=(
            "UNSIGNED_ADVISORY — требуется проверка и подпись квалифицированного инженера."
        ),
    )
