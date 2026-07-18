"""Optional post-rule LLM narrative with strict finding-number provenance (docs/14)."""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

from ..models import LlmNarrative
from .models import Finding, NormcheckSummary

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_RULE_ID = re.compile(r"\bR\d{2}\b")


class NarrativeClient(Protocol):
    def complete(
        self, *, model: str, system: str, user: str,
        json_schema: dict[str, Any] | None = None, max_output_tokens: int = 2048,
    ) -> str: ...


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


def check_finding_numeric_provenance(
    text: str, findings: list[Finding], summary: NormcheckSummary,
) -> tuple[bool, list[str]]:
    """Allow only typed finding/summary numbers; rule IDs and citation ids are removed."""
    allowed: set[float] = set()
    _collect_numbers([finding.model_dump() for finding in findings], allowed)
    _collect_numbers(summary.model_dump(), allowed)
    cleaned = _RULE_ID.sub(" ", text)
    for finding in findings:
        if finding.citation is None:
            continue
        for citation_part in (
            finding.citation.standard, finding.citation.clause, finding.citation.table,
        ):
            if citation_part:
                cleaned = cleaned.replace(citation_part, " ")
    unverified: list[str] = []
    for token in dict.fromkeys(_NUMBER.findall(cleaned)):
        value = float(token.replace(",", "."))
        if not any(abs(value - item) <= max(0.05, 0.01 * abs(item)) for item in allowed):
            unverified.append(token)
    return not unverified, unverified


def narrate_findings(
    findings: list[Finding], summary: NormcheckSummary, client: NarrativeClient, *, model: str,
) -> LlmNarrative | None:
    """Narrate immutable deterministic findings; reject any invented numeric value."""
    payload = {
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "summary": summary.model_dump(mode="json"),
    }
    text = client.complete(
        model=model,
        system=(
            "Ты объясняешь готовые детерминированные findings нормоконтроля. "
            "Не добавляй правил, выводов PASS и чисел, которых нет во входном JSON."
        ),
        user=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        max_output_tokens=800,
    )
    ok, unverified = check_finding_numeric_provenance(text, findings, summary)
    if not ok:
        return None
    return LlmNarrative(
        text=text, model=model, provenance_ok=True, unverified_numbers=unverified,
    )
