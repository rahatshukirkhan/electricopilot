"""Exception hierarchy for ElectriCopilot (frozen contract, docs/03 §3.7)."""
from __future__ import annotations


class ElectriCopilotError(Exception):
    """Base for all ElectriCopilot errors."""


class DataPackError(ElectriCopilotError):
    """Norm data pack failed to load / validate, or an off-table lookup was requested."""


class ProjectTopologyError(ElectriCopilotError):
    """A project supply/circuit topology is internally inconsistent."""

    code = "invalid_project_topology"


class LlmConfigError(ElectriCopilotError):
    """LLM is misconfigured: no API key in live mode, or an unknown model slug (D10)."""


class LlmError(ElectriCopilotError):
    """A live LLM call failed or returned empty content."""
