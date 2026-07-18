"""Shared fixtures. Tests never make network calls: LLM is exercised via client=None
(fallback) or a FakeClient, never a real OpenRouterClient (D8)."""
from __future__ import annotations

import pytest

from electricopilot.data.loader import NUMERIC_PROVENANCE_SECTIONS, DataPack, load_data_pack
from electricopilot.models import (
    DataSourceRecord,
    InstallationConditions,
    LoadSpec,
    ProtectionSpec,
    SizingRequest,
)


@pytest.fixture(scope="session")
def pack() -> DataPack:
    return load_data_pack()


def _verified_source(origin: str = "licensed") -> DataSourceRecord:
    return DataSourceRecord.model_validate({
        "origin": origin,
        "source_document": "TEST FIXTURE — verified numeric source",
        "source_url": "https://example.invalid/test-fixture",
        "entered_by": "test-entry",
        "verified_by": "test-reviewer",
        "verified_at": "2026-07-19T00:00:00Z",
    })


@pytest.fixture()
def verified_pack(pack: DataPack) -> DataPack:
    """Numerically identical test pack with complete provenance; never shipped as norm data."""
    return pack.model_copy(update={
        "provenance": {section: _verified_source() for section in NUMERIC_PROVENANCE_SECTIONS}
    })


@pytest.fixture()
def verified_public_pack() -> DataPack:
    """pue-rk numbers with test-only complete public-standard provenance."""
    pack = load_data_pack("pue-rk")
    return pack.model_copy(update={
        "provenance": {
            section: _verified_source("public_standard")
            for section in NUMERIC_PROVENANCE_SECTIONS
        }
    })


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
