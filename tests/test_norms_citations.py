"""Citation deep links and the PER-22 collision they cause (docs/20 §9, §12.5).

The point of this file: adding `doc_id`/`anchor` must be INVISIBLE where the numbers live
(exported bytes, numeric pack payload) and EXPLAINABLE where identity lives (manifest hash).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from electricopilot.calculation_manifest import (
    build_calculation_manifest,
    canonical_json_bytes,
    verify_calculation_manifest,
)
from electricopilot.data.loader import DataPack, load_data_pack
from electricopilot.models import Citation

from .test_calculation_manifest import _project

PACK_0_1_0 = Path(__file__).parent / "fixtures" / "packs" / "pue-rk-0.1.0.json"

# Snapshotted from the pack BEFORE the citation resolver ran (docs/20 §12.5). These are the
# identities every project, ZIP bundle and share link created before the bump carries.
GOLDEN_0_1_0 = {
    "norm_pack_sha256": "97adc1cc2ee37445bd135950022dcfd463bda356ca8ae442f58d1dca642776a0",
    "manifest_sha256": "536b96ca52613217c3055fae50a8ba6a5d15d5ca86b8798cb4b0931fecb7a2e2",
    "calculation_id": "calc-v1-536b96ca52613217c305",
}
# The same project on pue-rk 0.2.0 — different, predictably and for a stated reason.
GOLDEN_0_2_0 = {
    "norm_pack_sha256": "25881a1446e700b06810fd52b1b60c48859cc8800613dc5e5704e75416bc9d11",
    "manifest_sha256": "c505892497ef80a03718a001003790c9bfadd844f6261cb833e9139ddb604c38",
    "calculation_id": "calc-v1-c505892497ef80a03718",
}

PUE_DOC_ID = "V1500010851"


def _pack_0_1_0() -> DataPack:
    return DataPack.model_validate(json.loads(PACK_0_1_0.read_text(encoding="utf-8")))


def _numeric_payload(pack: dict[str, Any]) -> dict[str, Any]:
    """Everything except citation and meta blocks — the part that must not move."""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                key: strip(value)
                for key, value in node.items()
                if key not in {"_citation", "citations", "meta"}
            }
        if isinstance(node, list):
            return [strip(item) for item in node]
        return node

    return dict(strip(pack))


# --------------------------------------------------------------------- §9 Citation


def test_citation_stays_backward_compatible() -> None:
    plain = Citation(standard="ПУЭ РК", table="4")
    assert plain.doc_id is None and plain.anchor is None
    dumped = plain.model_dump()
    assert "doc_id" not in dumped and "anchor" not in dumped
    # existing optional fields keep their nulls — that is what makes the bytes identical
    assert dumped["clause"] is None and dumped["note"] is None


def test_resolved_citation_serializes_its_link() -> None:
    linked = Citation(standard="ПУЭ РК", table="4", doc_id=PUE_DOC_ID, anchor="z9362")
    dumped = linked.model_dump()
    assert dumped["doc_id"] == PUE_DOC_ID and dumped["anchor"] == "z9362"


def test_unresolved_links_are_omitted_inside_a_nested_result() -> None:
    """The serializer must apply through the parent dump, not only on a bare Citation."""
    from .conftest import make_request
    from electricopilot.engine import size

    result = size(
        make_request(U=230, ph=1, pf=0.95, P=3680, ins="PVC", amb=30, grp=1, L=18,
                     dev="MCB", iscc=1500),
        data_pack=load_data_pack("iec-stub"),
    )
    dumped = result.model_dump_json()
    assert '"doc_id"' not in dumped and '"anchor"' not in dumped
    assert '"clause":null' in dumped  # existing nulls untouched


# --------------------------------------------------------------------- §9 resolved pack


def test_pue_rk_citations_carry_verified_anchors() -> None:
    pack = json.loads(
        (Path(__file__).parents[1] / "src/electricopilot/data/packs/pue-rk.json").read_text(
            encoding="utf-8"
        )
    )
    assert pack["meta"]["version"] == "0.2.0"
    resolved = {
        "ampacity": pack["ampacity"]["_citation"],
        "ambient_correction": pack["ambient_correction"]["_citation"],
        "grouping_correction": pack["grouping_correction"]["_citation"],
        "k_material": pack["k_material"]["_citation"],
        "sc_adiabatic": pack["citations"]["sc_adiabatic"],
        "install_method": pack["citations"]["install_method"],
    }
    for name, citation in resolved.items():
        assert citation["doc_id"] == PUE_DOC_ID, name
        assert citation["anchor"].startswith("z"), name  # реальный z-якорь, не выдуманный

    # IEC-цитаты и vd_limits_pct не из ПУЭ — остаются без ссылок (docs/20 §9)
    for key in ("IB_load", "In_selection", "coord_overload", "voltage_drop"):
        assert "doc_id" not in pack["citations"][key], key


def test_links_reach_the_calculation_result() -> None:
    """Ради чего всё (docs/20 §9): аудит-трасса доходит не до названия стандарта, а до текста.

    Ссылка должна пережить путь пак → загрузчик → движок → Citation в результате, иначе
    «открыть пункт» в отчёте открывать нечего.
    """
    from .conftest import make_request
    from electricopilot.engine import size

    result = size(
        make_request(U=230, ph=1, pf=0.95, P=3680, ins="PVC", amb=30, grp=1, L=18,
                     dev="MCB", iscc=1500),
        data_pack=load_data_pack("pue-rk"),
    )
    linked = [
        citation
        for step in result.audit_trace
        for citation in step.citations
        if citation.doc_id
    ]
    assert linked, "ни одна цитата расчёта не получила ссылку на пункт нормы"
    assert all(c.doc_id == PUE_DOC_ID and c.anchor.startswith("z") for c in linked)
    # у ампакитности ссылка ведёт в Табл. 4, у КЗ — в Табл. 48
    anchors = {(c.table, c.anchor) for c in linked}
    assert ("4 (Cu), 5 (Al)", "z9362") in anchors
    assert ("48", "z11308") in anchors

    # IEC-цитаты остаются без ссылок и рендерятся как раньше
    iec = [c for step in result.audit_trace for c in step.citations if c.standard != "ПУЭ РК"]
    assert iec and all(c.doc_id is None and c.anchor is None for c in iec)


def test_numeric_sections_are_byte_identical_across_the_bump() -> None:
    """docs/20 §12.5: диф пака затрагивает только citations/_citation и meta."""
    old = json.loads(PACK_0_1_0.read_text(encoding="utf-8"))
    new = json.loads(
        (Path(__file__).parents[1] / "src/electricopilot/data/packs/pue-rk.json").read_text(
            encoding="utf-8"
        )
    )
    assert old["meta"]["version"] == "0.1.0" and new["meta"]["version"] == "0.2.0"
    assert canonical_json_bytes(_numeric_payload(old)) == canonical_json_bytes(
        _numeric_payload(new)
    )


def test_source_note_records_that_numbers_were_not_revisited() -> None:
    note = load_data_pack("pue-rk").meta.source_note
    assert "0.2.0" in note and "числовые значения не пересматривались" in note


# --------------------------------------------------------------------- §12.5 manifest


def test_manifest_golden_for_the_pre_bump_pack() -> None:
    manifest = build_calculation_manifest(_project(), _pack_0_1_0(), build_identity="build-golden")
    assert manifest.norm_pack.version == "0.1.0"
    assert manifest.norm_pack.sha256 == GOLDEN_0_1_0["norm_pack_sha256"]
    assert manifest.manifest_sha256 == GOLDEN_0_1_0["manifest_sha256"]
    assert manifest.calculation_id == GOLDEN_0_1_0["calculation_id"]


def test_manifest_golden_for_the_shipped_pack() -> None:
    manifest = build_calculation_manifest(
        _project(), load_data_pack("pue-rk"), build_identity="build-golden"
    )
    assert manifest.norm_pack.version == "0.2.0"
    assert manifest.norm_pack.sha256 == GOLDEN_0_2_0["norm_pack_sha256"]
    assert manifest.manifest_sha256 == GOLDEN_0_2_0["manifest_sha256"]
    assert manifest.calculation_id == GOLDEN_0_2_0["calculation_id"]


def test_the_hash_contract_is_not_weakened() -> None:
    """docs/20 §9: исключать цитаты из хеша запрещено — хеш обязан покрывать весь пак."""
    assert GOLDEN_0_1_0["norm_pack_sha256"] != GOLDEN_0_2_0["norm_pack_sha256"]
    assert GOLDEN_0_1_0["manifest_sha256"] != GOLDEN_0_2_0["manifest_sha256"]


def test_old_manifest_against_new_pack_explains_the_cause() -> None:
    """docs/20 §9: расхождение должно называть причину, а не выглядеть как сломанный расчёт."""
    old_manifest = build_calculation_manifest(
        _project(), _pack_0_1_0(), build_identity="build-golden"
    )
    verification = verify_calculation_manifest(
        old_manifest, _project(), load_data_pack("pue-rk"), build_identity="build-golden"
    )
    assert verification.ok is False
    assert set(verification.mismatches) == {"norm_pack.sha256", "manifest_sha256"}
    explanation = verification.explanation
    assert explanation is not None
    assert "0.1.0 → 0.2.0" in explanation
    assert "не пересчётом" in explanation
    assert "числовые значения не пересматривались" in explanation  # цитата из самого пака


def test_a_real_input_change_is_not_dressed_up_as_a_pack_bump() -> None:
    """Объяснение появляется ТОЛЬКО когда расхождение исчерпывается сменой пака."""
    pack = load_data_pack("pue-rk")
    manifest = build_calculation_manifest(_project(), pack, build_identity="build-golden")
    changed = _project()
    changed["circuits"][0]["request"]["installation"]["length_m"] = 25
    verification = verify_calculation_manifest(
        manifest, changed, pack, build_identity="build-golden"
    )
    assert verification.ok is False
    assert "project_sha256" in verification.mismatches
    assert verification.explanation is None


def test_matching_manifest_has_no_explanation() -> None:
    pack = load_data_pack("pue-rk")
    manifest = build_calculation_manifest(_project(), pack, build_identity="build-golden")
    verification = verify_calculation_manifest(
        manifest, _project(), pack, build_identity="build-golden"
    )
    assert verification.ok is True and verification.explanation is None


@pytest.mark.parametrize("pack_name", ["iec-stub", "pue-rk"])
def test_every_shipped_pack_still_validates(pack_name: str) -> None:
    assert load_data_pack(pack_name).meta.name == pack_name
