#!/usr/bin/env python3
"""Human-facing review of a norm data pack (docs/12 §1.2, docs/04 §4.6).

Prints every numeric table for eyeball comparison against the printed/official source, and
runs automated sanity checks: monotonicity of ampacity by section, monotonicity of correction
factors by temperature/grouping count, and outlier detection (>60% jump between adjacent
sections). This is the MANDATORY human checkpoint before a public_standard pack is committed —
flagged rows must be checked against the source before the pack is trusted.

Usage:  uv run python scripts/pack_review.py <pack-name>   # e.g. pue-rk
"""
from __future__ import annotations

import argparse
import json
import sys

from electricopilot.data.loader import DataPack, load_data_pack

OUTLIER_JUMP = 0.60  # flag a >60% jump between adjacent sections (docs/12 §1.2)


def _fnum(x: object) -> float | None:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def check_monotonic(labels: list[str], values: list[float | None], *, rising: bool = True) -> list[str]:
    issues = []
    prev = None
    for label, v in zip(labels, values):
        if v is None:
            continue
        if prev is not None:
            if rising and v < prev[1] - 1e-9:
                issues.append(f"НЕ монотонно: {prev[0]}={prev[1]:g} -> {label}={v:g} (должно расти)")
            if not rising and v > prev[1] + 1e-9:
                issues.append(f"НЕ монотонно: {prev[0]}={prev[1]:g} -> {label}={v:g} (должно убывать)")
        prev = (label, v)
    return issues


def check_outliers(labels: list[str], values: list[float | None]) -> list[str]:
    issues = []
    prev = None
    for label, v in zip(labels, values):
        if v is None:
            continue
        if prev is not None and prev[1] > 0:
            jump = abs(v - prev[1]) / prev[1]
            if jump > OUTLIER_JUMP:
                issues.append(
                    f"ПОДОЗРИТЕЛЬНЫЙ СКАЧОК ({jump:.0%}): {prev[0]}={prev[1]:g} -> {label}={v:g}"
                )
        prev = (label, v)
    return issues


def _print_row(title: str, labels: list[str], values: list[float | None]) -> list[str]:
    cells = "  ".join(f"{lb}={('—' if v is None else format(v, 'g'))}" for lb, v in zip(labels, values))
    print(f"  {title:32} {cells}")
    issues = check_monotonic(labels, values) + check_outliers(labels, values)
    for issue in issues:
        print(f"    ⚠ {title}: {issue}")
    return issues


def review_ampacity(pack: DataPack) -> list[str]:
    print("\n== ampacity (It, A) — монотонность по сечению внутри каждой колонки ==")
    all_issues = []
    sections = sorted(pack.standard_sections_mm2)
    labels = [format(s, "g") for s in sections]
    for method, by_material in pack.ampacity.items():
        if method == "_citation" or not isinstance(by_material, dict):
            continue
        for material, by_insulation in by_material.items():
            for insulation, by_n in by_insulation.items():
                for n, table in by_n.items():
                    values = [_fnum(table.get(lb)) for lb in labels]
                    title = f"{method}/{material}/{insulation}/n={n}"
                    all_issues += _print_row(title, labels, values)
    return all_issues


def review_ambient_correction(pack: DataPack) -> list[str]:
    print("\n== ambient_correction (ka) — монотонность по температуре (должна УБЫВАТЬ) ==")
    all_issues = []
    for insulation, table in pack.ambient_correction.items():
        if insulation == "_citation" or not isinstance(table, dict):
            continue
        labels = sorted((k for k in table if k != "_citation"), key=float)
        values = [_fnum(table[lb]) for lb in labels]
        title = f"ka/{insulation}"
        cells = "  ".join(f"{lb}°C={v:g}" for lb, v in zip(labels, values) if v is not None)
        print(f"  {title:32} {cells}")
        issues = check_monotonic(labels, values, rising=False)
        for issue in issues:
            print(f"    ⚠ {title}: {issue}")
        all_issues += issues
    return all_issues


def review_grouping_correction(pack: DataPack) -> list[str]:
    print("\n== grouping_correction (kg) — монотонность по количеству цепей (должна УБЫВАТЬ) ==")
    labels = sorted((k for k in pack.grouping_correction if k != "_citation"), key=float)
    values = [_fnum(pack.grouping_correction[lb]) for lb in labels]
    cells = "  ".join(f"{lb}={v:g}" for lb, v in zip(labels, values) if v is not None)
    print(f"  {'kg':32} {cells}")
    issues = check_monotonic(labels, values, rising=False)
    for issue in issues:
        print(f"    ⚠ kg: {issue}")
    return issues


def review_k_material(pack: DataPack) -> None:
    print("\n== k_material (адиабатика) ==")
    for key, v in pack.k_material.items():
        if key != "_citation":
            print(f"  {key:16} k={v:g}")


def review_citations(pack: DataPack) -> None:
    print("\n== источники (_citation) ==")
    sections = {
        "ampacity": pack.ampacity.get("_citation"),
        "ambient_correction": pack.ambient_correction.get("_citation"),
        "grouping_correction": pack.grouping_correction.get("_citation"),
        "k_material": pack.k_material.get("_citation"),
    }
    for name, cite in sections.items():
        if not cite:
            print(f"  {name}: (нет _citation!)")
            continue
        print(f"  {name}: {cite.get('standard')} {cite.get('table') or ''}")
        if cite.get("source_document"):
            print(f"    source_document: {cite['source_document']}")
        if cite.get("note"):
            print(f"    note: {cite['note']}")


def sanity_issues(pack: DataPack) -> list[str]:
    """Pure, machine-facing version of the numeric checks printed by the review views."""
    issues: list[str] = []
    sections = sorted(pack.standard_sections_mm2)
    labels = [format(s, "g") for s in sections]
    for method, by_material in pack.ampacity.items():
        if method == "_citation" or not isinstance(by_material, dict):
            continue
        for material, by_insulation in by_material.items():
            for insulation, by_n in by_insulation.items():
                for n, table in by_n.items():
                    values = [_fnum(table.get(label)) for label in labels]
                    prefix = f"{method}/{material}/{insulation}/n={n}: "
                    issues.extend(prefix + issue for issue in (
                        check_monotonic(labels, values) + check_outliers(labels, values)
                    ))
    for insulation, table in pack.ambient_correction.items():
        if insulation == "_citation" or not isinstance(table, dict):
            continue
        ambient_labels = sorted((k for k in table if k != "_citation"), key=float)
        values = [_fnum(table[label]) for label in ambient_labels]
        issues.extend(
            f"ka/{insulation}: {issue}"
            for issue in check_monotonic(ambient_labels, values, rising=False)
        )
    grouping_labels = sorted(
        (k for k in pack.grouping_correction if k != "_citation"), key=float
    )
    grouping_values = [_fnum(pack.grouping_correction[label]) for label in grouping_labels]
    issues.extend(
        f"kg: {issue}"
        for issue in check_monotonic(grouping_labels, grouping_values, rising=False)
    )
    return issues


def publication_payload(pack: DataPack) -> dict[str, object]:
    assessment = pack.publication_assessment()
    issues = sanity_issues(pack)
    return {
        "pack": pack.meta.name,
        "version": pack.meta.version,
        "origin": pack.meta.status,
        "verification_status": assessment.verification_status,
        "publication_ready": assessment.verification_status == "VERIFIED" and not issues,
        "untrusted_sections": assessment.untrusted_sections,
        "section_assessments": [a.model_dump() for a in assessment.used_sections],
        "sanity_issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pack", help="bundled pack name or JSON path")
    parser.add_argument("--json", action="store_true", help="machine-readable assessment")
    args = parser.parse_args()
    pack = load_data_pack(args.pack)
    payload = publication_payload(pack)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["publication_ready"] else 1

    print(f"pack: {pack.meta.name} {pack.meta.version} ({pack.meta.status})")
    print(f"source_note: {pack.meta.source_note}")
    if pack.meta.source_document:
        print(f"source_document: {pack.meta.source_document}")

    issues: list[str] = []
    issues += review_ampacity(pack)
    issues += review_ambient_correction(pack)
    issues += review_grouping_correction(pack)
    review_k_material(pack)
    review_citations(pack)

    assessment = pack.publication_assessment()
    print("\n== готовность к публикации ==")
    print(f"  verification_status: {assessment.verification_status}")
    print(f"  publication_ready: {payload['publication_ready']}")
    for section in assessment.used_sections:
        marker = "OK" if section.trusted else "NEEDS_REVIEW"
        details = "; ".join(section.issues)
        print(f"  {section.section}: {marker}{' — ' + details if details else ''}")

    print(f"\n== итог: {len(issues)} подозрительных мест ==")
    if issues:
        for issue in issues:
            print(f"  ⚠ {issue}")
        print("\nЭти значения нужно сверить с печатной/официальной редакцией источника ПЕРЕД коммитом.")
    else:
        print("Автоматические проверки не нашли монотонность-нарушений/выбросов. "
              "Ручная сверка таблиц выше с источником всё равно обязательна.")
    return 0 if payload["publication_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
