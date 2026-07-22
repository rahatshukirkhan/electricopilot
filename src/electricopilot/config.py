"""Environment configuration (docs/00 §0.5, docs/05).

Secrets come only from the environment / a local .env. The deterministic engine never
reads this module; only the CLI / live-LLM / persistence layers consult it to decide
live-vs-fallback. Tests must NOT rely on ambient env — they force fallback structurally.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()  # loads ./.env if present; no-op otherwise


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class Config:
    openrouter_api_key: str
    openrouter_base_url: str
    model_strong: str
    model_fast: str
    app_title: str
    http_referer: str
    database_url: str
    strict_provenance: bool
    llm_timeout: int
    max_request_bytes: int = 2 * 1024 * 1024
    max_project_circuits: int = 128
    max_concurrent_heavy_operations: int = 2
    llm_admission_mode: str = "local"
    llm_requests_per_window: int = 12
    llm_global_requests_per_window: int = 24
    llm_window_seconds: int = 60

    @property
    def llm_available(self) -> bool:
        return bool(self.openrouter_api_key.strip())

    @property
    def db_available(self) -> bool:
        return bool(self.database_url.strip())


def get_config() -> Config:
    # A process-local limiter is not a security control in serverless.  On Vercel,
    # live LLM is therefore disabled until the owner deliberately supplies a
    # distributed perimeter policy (docs/19); local runs retain bounded test/dev use.
    default_admission = "disabled" if os.environ.get("VERCEL") == "1" else "local"
    configured_admission = os.environ.get("ELECTRICOPILOT_LLM_ADMISSION_MODE", "").strip().lower()
    return Config(
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY", "").strip(),
        openrouter_base_url=os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        ).rstrip("/"),
        model_strong=os.environ.get("ELECTRICOPILOT_MODEL_STRONG", "google/gemini-3.1-pro-preview"),
        model_fast=os.environ.get("ELECTRICOPILOT_MODEL_FAST", "google/gemini-3-flash-preview"),
        app_title=os.environ.get("OPENROUTER_APP_TITLE", "ElectriCopilot"),
        http_referer=os.environ.get(
            "OPENROUTER_HTTP_REFERER", "https://github.com/rahatshukirkhan/electricopilot"
        ),
        database_url=os.environ.get("DATABASE_URL", "").strip(),
        strict_provenance=_bool("ELECTRICOPILOT_STRICT_PROVENANCE", True),
        llm_timeout=_positive_int("ELECTRICOPILOT_LLM_TIMEOUT", 60),
        max_request_bytes=_positive_int("ELECTRICOPILOT_MAX_REQUEST_BYTES", 2 * 1024 * 1024),
        max_project_circuits=_positive_int("ELECTRICOPILOT_MAX_PROJECT_CIRCUITS", 128),
        max_concurrent_heavy_operations=_positive_int(
            "ELECTRICOPILOT_MAX_CONCURRENT_HEAVY_OPERATIONS", 2
        ),
        llm_admission_mode=configured_admission or default_admission,
        llm_requests_per_window=_positive_int("ELECTRICOPILOT_LLM_REQUESTS_PER_WINDOW", 12),
        llm_global_requests_per_window=_positive_int(
            "ELECTRICOPILOT_LLM_GLOBAL_REQUESTS_PER_WINDOW", 24
        ),
        llm_window_seconds=_positive_int("ELECTRICOPILOT_LLM_WINDOW_SECONDS", 60),
    )
