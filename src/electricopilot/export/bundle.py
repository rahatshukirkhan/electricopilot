"""Bundle every board document into one in-memory zip (docs/13).

Contents: report.md, sld.svg, sld.dxf, cable_journal.xlsx, boq.xlsx, project.json (multi-sheet
diagrams add sld-2.*, sld-3.* …). Every document carries the advisory disclaimer and the data
pack's provenance note. Zip entries use a fixed timestamp; report.md, SVG and XLSX payloads are
byte-deterministic. Only DXF is not — ezdxf embeds its own GUIDs/timestamps — so the whole
archive is not byte-identical across runs.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Optional

from ..data.loader import DataPack, load_data_pack
from ..project import build_project_report
from ..project_contract import ProjectInput, project_payload, validate_project
from .boq import build_boq
from .cable_journal import build_cable_journal
from .dxf import render_dxf
from .sld import build_sld
from .svg import render_svg
from .xlsx import render_table_xlsx

_FIXED_DATE = (2020, 1, 1, 0, 0, 0)  # reproducible zip entries (no wall-clock)


def build_bundle(project: ProjectInput, *, data_pack: Optional[DataPack] = None) -> bytes:
    canonical = validate_project(project)
    payload = project_payload(canonical)
    pack = data_pack or load_data_pack(canonical.norm_pack)
    report = build_project_report(canonical, data_pack=pack)
    sheets = build_sld(canonical, report)

    files: dict[str, bytes] = {
        "report.md": report["markdown"].encode("utf-8"),
        "cable_journal.xlsx": render_table_xlsx(build_cable_journal(canonical, report)),
        "boq.xlsx": render_table_xlsx(build_boq(canonical, report)),
        "project.json": json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
    }
    for i, sheet in enumerate(sheets):
        suffix = "" if i == 0 else f"-{i + 1}"
        files[f"sld{suffix}.svg"] = render_svg(sheet).encode("utf-8")
        files[f"sld{suffix}.dxf"] = render_dxf(sheet)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(filename=name, date_time=_FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[name])
    return buf.getvalue()
