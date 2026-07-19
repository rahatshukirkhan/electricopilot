"""Exception hierarchy for ElectriCopilot (frozen contract, docs/03 §3.7)."""
from __future__ import annotations


class ElectriCopilotError(Exception):
    """Base for all ElectriCopilot errors."""


class DataPackError(ElectriCopilotError):
    """Norm data pack failed to load / validate, or an off-table lookup was requested."""


class ProjectTopologyError(ElectriCopilotError):
    """A project supply/circuit topology is internally inconsistent."""

    code = "invalid_project_topology"


class ProjectContractError(ElectriCopilotError):
    """A versioned project payload failed canonical schema validation."""

    code = "invalid_project_contract"

    def __init__(self, *, path: str, message: str) -> None:
        self.path = path
        super().__init__(f"Некорректный проект ({path or 'project'}): {message}")


class LlmConfigError(ElectriCopilotError):
    """LLM is misconfigured: no API key in live mode, or an unknown model slug (D10)."""


class LlmError(ElectriCopilotError):
    """A live LLM call failed or returned empty content."""
