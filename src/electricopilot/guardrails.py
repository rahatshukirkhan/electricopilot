"""Guardrails: numeric provenance of LLM narratives + advisory-status invariants (docs/05 §5.3).

A narrative may reference ONLY numbers that appear as TYPED numeric values in the audited
SizingResult (plus a small structural whitelist). Citation identifiers (IEC 60364-4-43,
§433.1, B.52.14…) are stripped from the text before checking, so they neither false-positive
nor let a smuggled engineering value ride on a citation digit. Any smuggled number is
reported; in strict mode a PASS result is downgraded to NEEDS_REVIEW. Computed numbers are
never altered.
"""
from __future__ import annotations

import re
from typing import Any

from .models import LlmNarrative, SizingResult

# Thousands separators: regular space, NBSP, thin space, narrow NBSP.
_GROUP_SEPS = (" ", " ", " ", " ")
_SEP_CLASS = "[" + "".join(_GROUP_SEPS) + "]"
# Grouped thousands ("25 000", "1 234 567") as ONE token, else a plain decimal.
_NUM = re.compile(
    r"\d{1,3}(?:" + _SEP_CLASS + r"\d{3})+(?:[.,]\d+)?"
    r"|\d+(?:[.,]\d+)?"
)
# Citation references removed from narratives before number extraction.
_CITE_PAT = re.compile(
    r"IEC\s*\d[\d.\-]*"            # IEC 60364-4-43, IEC 60898
    r"|§\s*\d[\dA-Za-z.]*"         # §433.1, §434.5.2
    r"|\bB\.\d[\d.]*"              # B.52.14
    r"|(?:Табл\.?|Table|Таблица)\s*[A-ZА-Я]?\.?[\d.]+\w*"  # Table 43A, Табл. B.52.17
    r"|Annex\s+\w+",
    re.IGNORECASE,
)
# Structural constants that legitimately appear in reasoning prose (phases, the 1.45 rule, √3, %).
_STRUCTURAL = {1.0, 2.0, 3.0, 1.45, 1.6, 1.3, 100.0, 1.73, 1.732}


def _to_float(tok: str) -> float | None:
    t = tok
    for ch in _GROUP_SEPS:
        t = t.replace(ch, "")
    try:
        return float(t.replace(",", "."))
    except ValueError:
        return None


def _collect_numbers(obj: Any, out: set[float]) -> None:
    """Harvest numbers from TYPED numeric leaves only. Strings are ignored on purpose:
    trace/citation text must not widen the allow-set (that is what let smuggled numbers ride
    on the ratings ladder and citation ids)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)
    # str: intentionally skipped


def _allowed_numbers(result: SizingResult) -> set[float]:
    allowed: set[float] = set(_STRUCTURAL)
    _collect_numbers(result.model_dump(), allowed)
    return allowed


def _citation_strings(result: SizingResult) -> set[str]:
    strs: set[str] = set()
    groups = [c for st in result.audit_trace for c in st.citations]
    groups += [c for ch in result.checks for c in ch.citations]
    for c in groups:
        for part in (c.standard, c.clause, c.table):
            if part:
                strs.add(part)
    return strs


def _matches(value: float, allowed: set[float]) -> bool:
    return any(abs(value - a) <= max(0.05, 0.01 * abs(a)) for a in allowed)


def check_numeric_provenance(
    text: str, result: SizingResult, *, strict: bool
) -> tuple[bool, list[str]]:
    """Return (provenance_ok, unverified_numbers). In strict mode any unverified number
    makes provenance_ok False; otherwise it is advisory only."""
    allowed = _allowed_numbers(result)
    cleaned = _CITE_PAT.sub(" ", text)
    for cite in _citation_strings(result):
        cleaned = cleaned.replace(cite, " ")
    unverified: list[str] = []
    seen: set[str] = set()
    for tok in _NUM.findall(cleaned):
        v = _to_float(tok)
        norm = tok.strip()
        if v is None or norm in seen:
            continue
        seen.add(norm)
        if not _matches(v, allowed):
            unverified.append(norm)
    ok = not (strict and unverified)
    return ok, unverified


def apply_provenance_downgrade(result: SizingResult, narrative: LlmNarrative) -> SizingResult:
    """Return a COPY downgraded to NEEDS_REVIEW when a PASS narrative failed provenance.
    A provenance failure must NEVER improve severity: a FAIL stays FAIL (warning appended).
    Never mutates in place; never touches the computed numbers."""
    if narrative.provenance_ok:
        return result
    warnings = [*result.warnings,
                f"LLM-нарратив не прошёл числовой провенанс: {narrative.unverified_numbers}"]
    new_status = "NEEDS_REVIEW" if result.overall_status == "PASS" else result.overall_status
    return result.model_copy(update={"overall_status": new_status, "warnings": warnings})
