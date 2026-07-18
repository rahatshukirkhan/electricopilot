"""ElectriCopilot Studio backend (docs/10). FastAPI: deterministic viz + server-side Gemini.

The OpenRouter key stays server-side; the browser only sends natural-language / parameters.
Requires the `api` extra (fastapi + uvicorn). Run locally:
    uv run uvicorn electricopilot.studio_api:app --reload --port 8000
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .config import get_config
from .data.loader import DataPack, list_packs, load_data_pack
from .engine import size
from .exceptions import DataPackError, LlmConfigError, LlmError
from .export import build_bundle, build_sld
from .export.svg import render_svg
from .guardrails import check_numeric_provenance
from .llm.client import OpenRouterClient
from .llm.explain import explain_render, explain_render_template
from .llm.intake import intake_parse
from .llm.verify import verify_deterministic_check, verify_review
from .models import SizingRequest, SizingResult, VerificationVerdict
from .project import build_project_report
from .viz import build_visuals

app = FastAPI(title="ElectriCopilot Studio", version="0.1.0",
              description="Auditable AI workbench for electrical design (IEC 60364). Advisory only.")


def _allowed_origins() -> list[str]:
    """Cross-origin allowlist for the API, from ELECTRICOPILOT_ALLOWED_ORIGINS
    (comma-separated). The Studio UI is served same-origin (const API = ''), so
    Vercel routes and local uvicorn-with-static never need a CORS entry — this
    only exists for a separately hosted frontend, and skipping it by default
    avoids exposing the server-side LLM key to arbitrary origins."""
    raw = os.environ.get("ELECTRICOPILOT_ALLOWED_ORIGINS", "").strip()
    return [o.strip() for o in raw.split(",") if o.strip()]


_origins = _allowed_origins()
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )


class IntakeBody(BaseModel):
    text: str


def _client() -> OpenRouterClient:
    return OpenRouterClient(get_config())


@app.get("/api/health")
def health() -> dict[str, str]:
    cfg = get_config()
    return {"status": "ok", "mode": "live" if cfg.llm_available else "fallback",
            "model_fast": cfg.model_fast, "model_strong": cfg.model_strong}


@app.get("/api/packs")
def packs_endpoint() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for meta in list_packs():
        pack = load_data_pack(meta.name)
        assessment = pack.publication_assessment()
        result.append({
            "name": meta.name,
            "version": meta.version,
            "status": meta.status,
            "source_note": meta.source_note,
            "verification_status": assessment.verification_status,
            "publication_ready": assessment.verification_status == "VERIFIED",
            "untrusted_sections": assessment.untrusted_sections,
        })
    return result


def _pack_or_400(pack: Optional[str]) -> DataPack:
    try:
        return load_data_pack(pack) if pack else load_data_pack()
    except DataPackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def _catch_pack_error(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Off-table combinations (e.g. a real pack's partial section coverage) raise
    DataPackError mid-calculation, not just at pack-resolution time — surface those as a
    clean 400 too, instead of an unhandled 500 with a leaked stack trace."""
    try:
        return fn(*args, **kwargs)
    except DataPackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/size", response_model=SizingResult)
def size_endpoint(request: SizingRequest, pack: Optional[str] = None) -> SizingResult:
    return _catch_pack_error(size, request, data_pack=_pack_or_400(pack))  # type: ignore[no-any-return]


@app.post("/api/viz")
def viz_endpoint(request: SizingRequest, pack: Optional[str] = None) -> dict[str, Any]:
    return _catch_pack_error(build_visuals, request, data_pack=_pack_or_400(pack))  # type: ignore[no-any-return]


@app.post("/api/intake")
def intake_endpoint(body: IntakeBody) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.llm_available:
        return {"ok": False, "error": "no_key",
                "message": "OPENROUTER_API_KEY не задан на сервере — NL-разбор недоступен."}
    try:
        req = intake_parse(body.text, _client(), model=cfg.model_fast)
        return {"ok": True, "request": req.model_dump(), "model": cfg.model_fast}
    except (LlmConfigError, LlmError, ValueError, ValidationError):
        return {"ok": False, "error": "intake_failed",
                "message": "Не удалось разобрать описание в параметры цепи — уточни формулировку "
                           "или задай параметры вручную."}


@app.post("/api/explain")
def explain_endpoint(request: SizingRequest, pack: Optional[str] = None) -> dict[str, Any]:
    cfg = get_config()
    result = _catch_pack_error(size, request, data_pack=_pack_or_400(pack))
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
def verify_endpoint(request: SizingRequest, pack: Optional[str] = None) -> dict[str, Any]:
    cfg = get_config()
    result = _catch_pack_error(size, request, data_pack=_pack_or_400(pack))
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


class ProjectBody(BaseModel):
    project: dict[str, Any]


def _report_for(project: dict[str, Any]) -> dict[str, Any]:
    pack = _pack_or_400(project.get("norm_pack"))
    return _catch_pack_error(build_project_report, project, data_pack=pack)  # type: ignore[no-any-return]


def _sld_preview(project: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """First-sheet SVG + sheet count. Only sheet 0 is rendered — the preview/print show one
    sheet; the full multi-sheet set lives in the export bundle (docs/13)."""
    sheets = build_sld(project, report)
    return {"svg": render_svg(sheets[0]) if sheets else "", "sheets": len(sheets)}


@app.post("/api/project-report")
def project_report_endpoint(body: ProjectBody, sld: bool = False) -> dict[str, Any]:
    """Recompute every circuit of a board with the real engine → panel schedule + totals + report.
    Norm pack is read from project.norm_pack (falls back to the default pack). With ?sld=1 the
    single-line preview is computed from the SAME report, so the board view/print need one call
    (one engine pass) instead of two."""
    report = _report_for(body.project)
    if sld:
        report = {**report, "sld": _sld_preview(body.project, report)}
    return report


def _safe_filename(project: dict[str, Any]) -> str:
    base = str(project.get("board_ref") or project.get("name") or "board")
    return re.sub(r"[^\w.-]+", "_", base).strip("_") or "board"


@app.post("/api/project-sld")
def project_sld_endpoint(body: ProjectBody) -> dict[str, Any]:
    """Single-line diagram preview: first-sheet SVG + sheet count (docs/13)."""
    return _sld_preview(body.project, _report_for(body.project))


@app.post("/api/project-export")
def project_export_endpoint(body: ProjectBody) -> Response:
    """Full document package as a downloadable zip (docs/13)."""
    pack = _pack_or_400(body.project.get("norm_pack"))
    data: bytes = _catch_pack_error(build_bundle, body.project, data_pack=pack)
    # HTTP headers are latin-1; RFC 5987 filename* must be percent-encoded UTF-8 (the board
    # ref can be Cyrillic), with an ASCII filename= fallback for old clients.
    fname = quote(f"{_safe_filename(body.project)}_пакет.zip")
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition":
                 f"attachment; filename=\"bundle.zip\"; filename*=UTF-8''{fname}"},
    )


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
