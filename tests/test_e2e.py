"""End-to-end offline slice: demo request → pipeline → rendered report (no network)."""
from __future__ import annotations

import json
from pathlib import Path

from electricopilot.models import SizingRequest
from electricopilot.pipeline import run
from electricopilot.report import render_json, render_markdown

EXAMPLE = Path(__file__).parents[1] / "src/electricopilot/data/examples/motor_feeder.json"


def test_demo_request_end_to_end_offline():
    req = SizingRequest.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))
    out = run(req, client=None, explain=True, verify=True)
    r = out.result
    assert r.overall_status in ("PASS", "FAIL", "NEEDS_REVIEW")
    # demo is the gG-fuse feeder → 10 mm², In 32 A, overload-bound
    assert r.selected_cable.cross_section_mm2 == 10
    assert r.selected_protection.In_a == 32
    assert r.selected_cable.governing_constraint == "overload_coordination"

    md = render_markdown(r, out.narrative, out.verdict)
    for token in ("# ElectriCopilot", "Проверки", "Трасса", "Подпись инженера",
                  "НЕ ПОДПИСАНО", "СИНТЕТИЧЕСК"):
        assert token in md or token in md.upper(), token
    # JSON round-trips
    parsed = json.loads(render_json(r))
    assert parsed["overall_status"] == r.overall_status
    assert parsed["data_provenance_note"]  # non-empty for illustrative pack


def test_result_always_has_three_checks():
    req = SizingRequest.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))
    r = run(req, client=None).result
    names = {c.name for c in r.checks}
    assert names == {"overload_coordination", "voltage_drop", "short_circuit"}
