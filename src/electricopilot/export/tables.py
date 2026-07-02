"""Shared tabular model for XLSX documents (docs/13). cable_journal.py and boq.py build a
Table; xlsx.py renders it. Keeps document content decoupled from the openpyxl renderer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Table:
    title: str
    columns: list[str]
    rows: list[list[Any]]
    notes: list[str] = field(default_factory=list)
