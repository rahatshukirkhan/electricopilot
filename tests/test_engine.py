"""Unit tests for engine building blocks."""
from __future__ import annotations

import math

import pytest

from electricopilot.engine.ampacity import corrected_ampacity
from electricopilot.engine.current import design_current
from electricopilot.engine.protection import select_rating
from electricopilot.engine.shortcircuit import adiabatic_min_section
from electricopilot.engine.voltage_drop import voltage_drop
from electricopilot.models import (
    InstallationConditions,
    LoadSpec,
    ProtectionSpec,
)


def test_design_current_single_phase(pack):
    ib, step = design_current(LoadSpec(power_w=4600, voltage_v=230, phases=1, power_factor=1.0), pack)
    assert ib == pytest.approx(20.0)
    assert step.id == "current" and step.citations


def test_design_current_three_phase(pack):
    ib, _ = design_current(LoadSpec(power_w=15000, voltage_v=400, phases=3, power_factor=0.85), pack)
    assert ib == pytest.approx(15000 / (math.sqrt(3) * 400 * 0.85), abs=0.01)


def test_design_current_from_current(pack):
    ib, _ = design_current(LoadSpec(current_a=100, voltage_v=400, phases=3), pack)
    assert ib == 100


def test_select_rating(pack):
    assert select_rating(20.0, "MCB", pack)[0] == 20
    assert select_rating(21.0, "MCB", pack)[0] == 25
    assert select_rating(25.47, "gG_fuse", pack)[0] == 32


def test_corrected_ampacity(pack):
    cond = InstallationConditions(method="C", material="Cu", insulation="PVC",
                                  ambient_temp_c=35, grouping_circuits=1, length_m=25,
                                  loaded_conductors=2)
    it, prodk, iz, step = corrected_ampacity(4.0, cond, pack)
    assert it == 30 and prodk == pytest.approx(0.90) and iz == pytest.approx(27.0)


def test_voltage_drop_three_phase_lower_than_single(pack):
    load1 = LoadSpec(current_a=20, voltage_v=230, phases=1, power_factor=1.0)
    cond = InstallationConditions(method="C", length_m=25, loaded_conductors=2)
    _, pct, step = voltage_drop(load1, cond, 4.0, 20.0, pack)
    assert pct == pytest.approx(2.45, abs=0.02)
    assert step.citations


def test_adiabatic(pack):
    cond = InstallationConditions(method="C", material="Cu", insulation="XLPE", length_m=30)
    smin, step = adiabatic_min_section(
        ProtectionSpec(prospective_fault_current_a=20000, disconnection_time_s=0.2), cond, pack)
    assert smin == pytest.approx(59.63, abs=0.02)
    # skipped when no fault current
    smin2, step2 = adiabatic_min_section(ProtectionSpec(), cond, pack)
    assert smin2 is None and step2.status == "warning"
