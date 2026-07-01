"""Data pack loader / accessors / off-table policy."""
from __future__ import annotations

import pytest

from electricopilot.data.loader import load_data_pack, pack_key
from electricopilot.exceptions import DataPackError


def test_loads_and_is_illustrative(pack):
    assert pack.meta.status == "illustrative"
    assert "SYNTHETIC" in pack.meta.source_note.upper()
    assert len(pack.ratings()) == 18 and len(pack.sections()) == 16


def test_pack_key():
    assert pack_key(4.0) == "4"
    assert pack_key(2.5) == "2.5"
    assert pack_key(35.0) == "35"


def test_values_are_synthetic_not_real_iec(pack):
    # real IEC method-C Cu/PVC 2-cond @2.5mm² is 27 A; synthetic pack must differ.
    it, _ = pack.ampacity_it("C", "Cu", "PVC", 2, 2.5)
    assert it == 20 and it != 27


def test_rating_for(pack):
    assert pack.rating_for(20.0)[0] == 20
    assert pack.rating_for(20.1)[0] == 25


def test_off_table_ambient_raises(pack):
    with pytest.raises(DataPackError):
        pack.ambient_factor("PVC", 33.0)


def test_off_table_grouping_raises(pack):
    with pytest.raises(DataPackError):
        pack.grouping_factor(99)


def test_missing_ampacity_combo_raises(pack):
    with pytest.raises(DataPackError):
        pack.ampacity_it("D", "Al", "PVC", 3, 10.0)


def test_bad_path_raises():
    with pytest.raises(DataPackError):
        load_data_pack("/nonexistent/pack.json")
