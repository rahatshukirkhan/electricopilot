"""Optional HTTP surface (docs/03 §3.9). Requires the `api` extra (fastapi + uvicorn).

    uv run uvicorn electricopilot.api:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI

from .config import get_config
from .engine import size
from .models import SizingRequest, SizingResult

app = FastAPI(title="ElectriCopilot", version="0.1.0",
              description="Auditable IEC 60364 cable & protective-device sizing (advisory).")


@app.get("/health")
def health() -> dict[str, str]:
    cfg = get_config()
    return {"status": "ok", "mode": "live" if cfg.llm_available else "fallback"}


@app.post("/size", response_model=SizingResult)
def size_endpoint(request: SizingRequest) -> SizingResult:
    """Deterministic sizing. LLM roles are CLI-side; the API returns the audited numbers."""
    return size(request)
