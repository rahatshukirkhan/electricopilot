"""Contract validators (docs/03 §3.3)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from electricopilot.models import InstallationConditions, LoadSpec


def test_exactly_one_current_source_required():
    with pytest.raises(ValidationError):
        LoadSpec(voltage_v=230)  # neither power nor current
    with pytest.raises(ValidationError):
        LoadSpec(power_w=1000, current_a=5, voltage_v=230)  # both
    LoadSpec(power_w=1000, voltage_v=230)  # ok
    LoadSpec(current_a=5, voltage_v=230)   # ok


def test_invalid_enum_rejected():
    with pytest.raises(ValidationError):
        InstallationConditions(method="Z9", length_m=10)  # not a valid method


def test_phases_constrained():
    with pytest.raises(ValidationError):
        LoadSpec(power_w=1000, voltage_v=230, phases=2)  # only 1 or 3
