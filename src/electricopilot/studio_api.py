"""ElectriCopilot Studio backend (docs/10). FastAPI: deterministic viz + server-side Gemini.

The OpenRouter key stays server-side; the browser only sends natural-language / parameters.
Requires the `api` extra (fastapi + uvicorn). Run locally:
    uv run uvicorn electricopilot.studio_api:app --reload --port 8000
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import get_config
from .engine import size
from .exceptions import LlmConfigError, LlmError
from .guardrails import check_numeric_provenance
from .llm.client import OpenRouterClient
from .llm.explain import explain_render, explain_render_template
from .llm.intake import intake_parse
from .llm.verify import verify_deterministic_check, verify_review
from .models import SizingRequest, SizingResult, VerificationVerdict
from .viz import build_visuals

app = FastAPI(title="ElectriCopilot Studio", version="0.1.0",
              description="Auditable AI workbench for electrical design (IEC 60364). Advisory only.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class IntakeBody(BaseModel):
    text: str


def _client() -> OpenRouterClient:
    return OpenRouterClient(get_config())


@app.get("/api/health")
def health() -> dict[str, str]:
    cfg = get_config()
    return {"status": "ok", "mode": "live" if cfg.llm_available else "fallback",
            "model_fast": cfg.model_fast, "model_strong": cfg.model_strong}


@app.post("/api/size", response_model=SizingResult)
def size_endpoint(request: SizingRequest) -> SizingResult:
    return size(request)


@app.post("/api/viz")
def viz_endpoint(request: SizingRequest) -> dict[str, Any]:
    return build_visuals(request)


@app.post("/api/intake")
def intake_endpoint(body: IntakeBody) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.llm_available:
        return {"ok": False, "error": "no_key",
                "message": "OPENROUTER_API_KEY не задан на сервере — NL-разбор недоступен."}
    try:
        req = intake_parse(body.text, _client(), model=cfg.model_fast)
        return {"ok": True, "request": req.model_dump(), "model": cfg.model_fast}
    except (LlmConfigError, LlmError, ValueError) as exc:
        return {"ok": False, "error": "intake_failed", "message": str(exc)}


@app.post("/api/explain")
def explain_endpoint(request: SizingRequest) -> dict[str, Any]:
    cfg = get_config()
    result = size(request)
    if cfg.llm_available:
        try:
            narrative = explain_render(result, _client(), model=cfg.model_fast)
            ok, unv = check_numeric_provenance(narrative.text, result, strict=cfg.strict_provenance)
            narrative = narrative.model_copy(update={"provenance_ok": ok, "unverified_numbers": unv})
        except (LlmConfigError, LlmError):
            narrative = explain_render_template(result)
    else:
        narrative = explain_render_template(result)
    return {"narrative": narrative.model_dump(), "overall_status": result.overall_status}


@app.post("/api/verify")
def verify_endpoint(request: SizingRequest) -> dict[str, Any]:
    cfg = get_config()
    result = size(request)
    det_ok = verify_deterministic_check(request, result)
    if cfg.llm_available:
        try:
            verdict = verify_review(request, result, _client(), model=cfg.model_strong)
        except (LlmConfigError, LlmError):
            verdict = VerificationVerdict(agrees=det_ok, issues=["живой ревьюер недоступен"],
                                          model="", deterministic_ok=det_ok)
    else:
        verdict = VerificationVerdict(agrees=det_ok, model="", deterministic_ok=det_ok)
    return {"verdict": verdict.model_dump()}


# --- static frontend (local dev; on Vercel the web/ dir is served as static) ---
def _web_dir() -> Optional[Path]:
    env = os.environ.get("ELECTRICOPILOT_WEB_DIR")
    candidates = [Path(env)] if env else []
    candidates += [Path.cwd() / "web", Path(__file__).resolve().parents[2] / "web"]
    for c in candidates:
        if c.is_dir():
            return c
    return None


_web = _web_dir()
if _web is not None:
    app.mount("/", StaticFiles(directory=str(_web), html=True), name="web")
