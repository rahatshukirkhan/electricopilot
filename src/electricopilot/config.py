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

    @property
    def llm_available(self) -> bool:
        return bool(self.openrouter_api_key.strip())

    @property
    def db_available(self) -> bool:
        return bool(self.database_url.strip())


def get_config() -> Config:
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
        llm_timeout=int(os.environ.get("ELECTRICOPILOT_LLM_TIMEOUT", "60")),
    )
