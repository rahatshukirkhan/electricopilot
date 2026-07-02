"""Render a Table to a single-sheet XLSX workbook via openpyxl (docs/13).

Layout: title row, bold header, data rows, then notes (disclaimer + provenance). Column widths
are sized to content. Deterministic — no timestamps written into the sheet.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .tables import Table

_HEADER_FILL = PatternFill("solid", fgColor="E8EEF7")
_TITLE_FONT = Font(bold=True, size=13)
_HEADER_FONT = Font(bold=True)
_NOTE_FONT = Font(italic=True, size=9, color="555555")


def render_table_xlsx(table: Table) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = table.title[:31] or "Лист1"
    ncol = max(len(table.columns), 1)

    ws.append([table.title])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    ws.cell(row=1, column=1).font = _TITLE_FONT

    ws.append(table.columns)
    for c in range(1, ncol + 1):
        cell = ws.cell(row=2, column=c)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for row in table.rows:
        ws.append(list(row))

    ws.append([])
    for note in table.notes:
        r = ws.max_row + 1
        ws.cell(row=r, column=1, value=note).font = _NOTE_FONT
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncol)

    # column widths from content
    for c in range(1, ncol + 1):
        header = table.columns[c - 1] if c - 1 < len(table.columns) else ""
        width = len(str(header))
        for row in table.rows:
            if c - 1 < len(row):
                width = max(width, len(str(row[c - 1])))
        ws.column_dimensions[get_column_letter(c)].width = min(max(width + 2, 10), 48)

    ws.freeze_panes = "A3"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
