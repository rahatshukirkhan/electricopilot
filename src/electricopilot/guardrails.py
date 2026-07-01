"""Guardrails: numeric provenance of LLM narratives + advisory-status invariants (docs/05 §5.3).

A narrative may reference ONLY numbers that appear somewhere in the audited SizingResult
(values, formulas, citation ids). Any smuggled number is reported; in strict mode the result
is downgraded to NEEDS_REVIEW. The deterministic numbers themselves are never altered.
"""
from __future__ import annotations

import re
from typing import Any

from .models import LlmNarrative, SizingResult

_NUM = re.compile(r"\d+(?:[.,]\d+)?")
# Structural constants that legitimately appear in reasoning prose.
_STRUCTURAL = {0.0, 1.0, 2.0, 3.0, 1.45, 1.6, 1.3, 100.0, 1.73, 1.732}


def _to_float(tok: str) -> float | None:
    try:
        return float(tok.replace(",", "."))
    except ValueError:
        return None


def _collect_numbers(obj: Any, out: set[float]) -> None:
    """Recursively harvest every number reachable in the result, including numeric
    substrings of strings (this captures citation ids like 60364 / 433.1 and formula
    numbers embedded in detail/computation text)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        for m in _NUM.findall(obj):
            v = _to_float(m)
            if v is not None:
                out.add(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def _allowed_numbers(result: SizingResult) -> set[float]:
    allowed: set[float] = set(_STRUCTURAL)
    _collect_numbers(result.model_dump(), allowed)
    return allowed


def _matches(value: float, allowed: set[float]) -> bool:
    for a in allowed:
        if abs(value - a) <= max(0.05, 0.01 * abs(a)):
            return True
    return False


def check_numeric_provenance(
    text: str, result: SizingResult, *, strict: bool
) -> tuple[bool, list[str]]:
    """Return (provenance_ok, unverified_numbers). In strict mode any unverified number
    makes provenance_ok False; otherwise it is advisory only."""
    allowed = _allowed_numbers(result)
    unverified: list[str] = []
    seen: set[str] = set()
    for tok in _NUM.findall(text):
        v = _to_float(tok)
        if v is None or tok in seen:
            continue
        seen.add(tok)
        if not _matches(v, allowed):
            unverified.append(tok)
    ok = not (strict and unverified)
    return ok, unverified


def apply_provenance_downgrade(result: SizingResult, narrative: LlmNarrative) -> SizingResult:
    """Return a COPY downgraded to NEEDS_REVIEW when the narrative failed provenance.
    Never mutates in place; never touches the computed numbers."""
    if narrative.provenance_ok:
        return result
    warnings = [*result.warnings,
                f"LLM-нарратив не прошёл числовой провенанс: {narrative.unverified_numbers}"]
    return result.model_copy(update={"overall_status": "NEEDS_REVIEW", "warnings": warnings})
