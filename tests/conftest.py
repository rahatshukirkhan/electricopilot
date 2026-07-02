"""Shared fixtures. Tests never make network calls: LLM is exercised via client=None
(fallback) or a FakeClient, never a real OpenRouterClient (D8)."""
from __future__ import annotations

import pytest

from electricopilot.data.loader import DataPack, load_data_pack
from electricopilot.models import (
    InstallationConditions,
    LoadSpec,
    ProtectionSpec,
    SizingRequest,
)


@pytest.fixture(scope="session")
def pack() -> DataPack:
    return load_data_pack()


def make_request(**k) -> SizingRequest:
    return SizingRequest(
        load=LoadSpec(power_w=k.get("P"), current_a=k.get("I"), voltage_v=k["U"],
                      phases=k["ph"], power_factor=k["pf"], purpose=k.get("purpose", "power")),
        installation=InstallationConditions(
            method=k.get("method", "C"), material=k.get("material", "Cu"), insulation=k["ins"],
            ambient_temp_c=k["amb"], grouping_circuits=k["grp"], length_m=k["L"]),
        protection=ProtectionSpec(device_class=k["dev"], prospective_fault_current_a=k.get("iscc"),
                      disconnection_time_s=k.get("t", 0.1)))


class FakeClient:
    """Duck-typed OpenRouterClient for the live code path — no network."""

    def __init__(self, response: str, available: bool = True) -> None:
        self._response = response
        self.available = available
        self.calls = 0

    def complete(self, **_: object) -> str:
        self.calls += 1
        return self._response
