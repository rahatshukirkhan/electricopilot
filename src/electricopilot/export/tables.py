"""Shared tabular model for XLSX documents (docs/13). cable_journal.py and boq.py build a
Table; xlsx.py renders it. Keeps document content decoupled from the openpyxl renderer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def fmt_num(v: Any) -> str:
    """Compact number formatting shared by every export renderer (SLD labels, journal, BoQ):
    `:g` trims trailing zeros (20.0→'20', 2.5→'2.5'); non-numbers pass through as str."""
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


@dataclass
class Table:
    title: str
    columns: list[str]
    rows: list[list[Any]]
    notes: list[str] = field(default_factory=list)
