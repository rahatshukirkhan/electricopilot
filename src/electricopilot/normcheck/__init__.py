"""Deterministic board normcheck public API (docs/14)."""

from .models import Finding, NormcheckReport, NormcheckSummary
from .narrate import check_finding_numeric_provenance, narrate_findings
from .runner import build_normcheck_report, run_normcheck, summarize_findings
from .rules import RULES

__all__ = [
    "Finding",
    "NormcheckReport",
    "NormcheckSummary",
    "RULES",
    "build_normcheck_report",
    "run_normcheck",
    "summarize_findings",
    "check_finding_numeric_provenance",
    "narrate_findings",
]
