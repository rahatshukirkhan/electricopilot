"""Bundle every board document into one in-memory zip (docs/13).

Contents include the human documents, source project, typed manifest, and canonical calculation
input/data-pack payloads (docs/18). Every rendered document carries the advisory disclaimer,
calculation id and data-pack provenance note. Zip entries use a fixed timestamp.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Optional

from ..calculation_manifest import (
    CalculationManifest,
    canonical_json_bytes,
    canonical_pack_payload,
    effective_project_input,
)
from ..data.loader import DataPack, load_pack_by_name
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
    pack = data_pack or load_pack_by_name(canonical.norm_pack)
    report = build_project_report(canonical, data_pack=pack)
    manifest = CalculationManifest.model_validate(report["calculation_manifest"])
    sheets = build_sld(canonical, report)

    files: dict[str, bytes] = {
        "report.md": report["markdown"].encode("utf-8"),
        "cable_journal.xlsx": render_table_xlsx(build_cable_journal(canonical, report)),
        "boq.xlsx": render_table_xlsx(build_boq(canonical, report)),
        "project.json": json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        "manifest.json": canonical_json_bytes(manifest.model_dump(mode="json")),
        "calculation-input.json": canonical_json_bytes(effective_project_input(canonical)),
        "norm-pack.json": canonical_json_bytes(canonical_pack_payload(pack)),
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
