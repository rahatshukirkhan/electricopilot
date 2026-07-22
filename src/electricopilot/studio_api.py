"""ElectriCopilot Studio backend (docs/10). FastAPI: deterministic viz + server-side Gemini.

The OpenRouter key stays server-side; the browser only sends natural-language / parameters.
Requires the `api` extra (fastapi + uvicorn). Run locally:
    uv run uvicorn electricopilot.studio_api:app --reload --port 8000
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, cast
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, model_validator

from .config import get_config
from .admission import admission, rejected
from .copilot import CopilotRequest, run_copilot
from .data.loader import DataPack, list_packs, load_data_pack
from .engine import size
from .exceptions import (
    DataPackError,
    LlmConfigError,
    LlmError,
    ProjectContractError,
    ProjectTopologyError,
)
from .export import build_bundle, build_sld
from .export.svg import render_svg
from .guardrails import check_numeric_provenance
from .import_schedule import MAX_IMPORT_BYTES, ScheduleImportError, import_schedule
from .llm.client import OpenRouterClient
from .llm.explain import explain_render, explain_render_template
from .llm.intake import intake_parse
from .llm.verify import verify_deterministic_check, verify_review
from .models import SizingRequest, SizingResult, VerificationVerdict
from .normcheck import build_normcheck_report, narrate_findings
from .project import build_project_report
from .project_contract import Project, project_payload
from .store import (
    PostgresStore,
    ProjectConflictError,
    ProjectNotFoundError,
    ProjectRecord,
    ProjectStore,
)
from .viz import build_visuals

app = FastAPI(title="ElectriCopilot Studio", version="0.1.0",
              description="Auditable AI workbench for electrical design (IEC 60364). Advisory only.")


@app.middleware("http")
async def request_size_limit(request: Request, call_next: Any) -> Response:
    """Reject declared oversized input before FastAPI parses it or an endpoint allocates ZIP.

    HTTP/1.1 clients without a Content-Length are rejected for body-bearing methods:
    streaming them into a public JSON/multipart API would defeat a pre-allocation bound.
    """
    if request.method in {"POST", "PUT", "PATCH"}:
        raw_length = request.headers.get("content-length")
        if raw_length is None:
            return JSONResponse(
                status_code=411,
                content={"detail": {"ok": False, "error": "content_length_required",
                                    "message": "Для запроса требуется Content-Length."}},
            )
        try:
            length = int(raw_length)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": {"ok": False, "error": "invalid_content_length",
                                    "message": "Некорректный Content-Length."}},
            )
        if length < 0 or length > getattr(get_config(), "max_request_bytes", 2 * 1024 * 1024):
            return JSONResponse(
                status_code=413,
                content={"detail": {"ok": False, "error": "request_too_large",
                                    "message": "Размер запроса превышает допустимый лимит."}},
            )
    return cast(Response, await call_next(request))


@app.exception_handler(RequestValidationError)
async def _typed_project_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    """Project bodies expose one stable code/path instead of FastAPI's generic error list."""
    for problem in exc.errors():
        location = problem.get("loc", ())
        if len(location) >= 2 and location[0] == "body" and location[1] == "project":
            path = ".".join(str(part) for part in location[1:])
            message = str(problem.get("msg", "validation failed"))
            return JSONResponse(
                status_code=422,
                content={
                    "detail": {
                        "code": ProjectContractError.code,
                        "path": path,
                        "message": f"Некорректный проект: {message}",
                    }
                },
            )
    return await request_validation_exception_handler(request, exc)


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
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Workspace"],
    )


class IntakeBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


def _client() -> OpenRouterClient:
    return OpenRouterClient(get_config())


def _admit_llm(request: Request) -> None:
    rejection = admission.admit_llm(request, get_config())
    if rejection is not None:
        raise rejected(rejection)


@contextmanager
def _heavy_operation() -> Any:
    """Bound one process's costly calculations; Vercel perimeter remains separate."""
    if not admission.acquire_heavy(get_config()):
        raise HTTPException(
            status_code=429,
            detail={"ok": False, "error": "heavy_concurrency",
                    "message": "Слишком много тяжёлых операций. Повторите позже."},
            headers={"Retry-After": "1"},
        )
    try:
        yield
    finally:
        admission.release_heavy()


@app.get("/api/health")
def health() -> dict[str, str]:
    cfg = get_config()
    return {"status": "ok", "mode": "live" if cfg.llm_available else "fallback",
            "model_fast": cfg.model_fast, "model_strong": cfg.model_strong,
            "project_store": "neon" if cfg.db_available else "local"}


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


def _catch_project_error(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Surface deterministic project validation as a stable typed 4xx response."""
    try:
        return _catch_pack_error(fn, *args, **kwargs)
    except ProjectTopologyError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    except ProjectContractError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "path": exc.path, "message": str(exc)},
        ) from None


@app.post("/api/size", response_model=SizingResult)
def size_endpoint(request: SizingRequest, pack: Optional[str] = None) -> SizingResult:
    return _catch_pack_error(size, request, data_pack=_pack_or_400(pack))  # type: ignore[no-any-return]


@app.post("/api/viz")
def viz_endpoint(request: SizingRequest, pack: Optional[str] = None) -> dict[str, Any]:
    return _catch_pack_error(build_visuals, request, data_pack=_pack_or_400(pack))  # type: ignore[no-any-return]


@app.post("/api/intake")
def intake_endpoint(body: IntakeBody, request: Request) -> dict[str, Any]:
    cfg = get_config()
    if not cfg.llm_available:
        return {"ok": False, "error": "no_key",
                "message": "OPENROUTER_API_KEY не задан на сервере — NL-разбор недоступен."}
    _admit_llm(request)
    try:
        req = intake_parse(body.text, _client(), model=cfg.model_fast)
        return {"ok": True, "request": req.model_dump(), "model": cfg.model_fast}
    except (LlmConfigError, LlmError, ValueError, ValidationError):
        return {"ok": False, "error": "intake_failed",
                "message": "Не удалось разобрать описание в параметры цепи — уточни формулировку "
                           "или задай параметры вручную."}


@app.post("/api/explain")
def explain_endpoint(request: SizingRequest, http_request: Request, pack: Optional[str] = None) -> dict[str, Any]:
    cfg = get_config()
    result = _catch_pack_error(size, request, data_pack=_pack_or_400(pack))
    if cfg.llm_available:
        _admit_llm(http_request)
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
def verify_endpoint(request: SizingRequest, http_request: Request, pack: Optional[str] = None) -> dict[str, Any]:
    cfg = get_config()
    result = _catch_pack_error(size, request, data_pack=_pack_or_400(pack))
    det_ok = verify_deterministic_check(request, result)
    if cfg.llm_available:
        _admit_llm(http_request)
        try:
            verdict = verify_review(request, result, _client(), model=cfg.model_strong)
        except (LlmConfigError, LlmError):
            verdict = VerificationVerdict(agrees=det_ok, issues=["живой ревьюер недоступен"],
                                          model="", deterministic_ok=det_ok)
    else:
        verdict = VerificationVerdict(agrees=det_ok, model="", deterministic_ok=det_ok)
    return {"verdict": verdict.model_dump()}


class ProjectBody(BaseModel):
    project: Project

    @model_validator(mode="after")
    def _bounded_circuits(self) -> "ProjectBody":
        if len(self.project.circuits) > getattr(get_config(), "max_project_circuits", 128):
            raise ValueError("project exceeds configured circuit limit")
        return self


@app.post("/api/copilot")
def copilot_endpoint(body: CopilotRequest, request: Request) -> dict[str, Any]:
    """Return a bounded, read-only Copilot proposal; never write ProjectStore."""
    cfg = get_config()
    if not cfg.llm_available:
        return {
            "ok": False,
            "reply": (
                "OPENROUTER_API_KEY не задан — Copilot щита недоступен. "
                "Ручное редактирование, детерминированный расчёт и нормоконтроль работают."
            ),
            "proposal": None,
            "model": "",
            "provenance_ok": True,
            "unverified_numbers": [],
            "incomplete": False,
            "error": "no_key",
        }
    _admit_llm(request)
    response = run_copilot(
        body.project,
        body.message,
        body.history,
        client=_client(),
        model=cfg.model_strong,
        parse_model=cfg.model_fast,
    )
    payload = response.model_dump(mode="json")
    if response.error in {"invalid_tool", "invalid_proposal"}:
        raise HTTPException(status_code=422, detail={
            "code": response.error,
            "message": response.reply,
            "response": payload,
        })
    return payload


_PROJECT_STORE_OVERRIDE: ProjectStore | None = None


def _project_store() -> ProjectStore:
    if _PROJECT_STORE_OVERRIDE is not None:
        return _PROJECT_STORE_OVERRIDE
    cfg = get_config()
    if not cfg.db_available:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error": "no_db", "message": "DATABASE_URL не задан."},
        )
    return PostgresStore(cfg.database_url)


def _workspace(value: str | None) -> str:
    workspace = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", workspace):
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "workspace_required",
                "message": "Требуется непустой заголовок X-Workspace.",
            },
        )
    return workspace


def _stored_payload(record: ProjectRecord) -> dict[str, Any]:
    return record.payload()


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"ok": False, "error": "project_not_found", "message": "Проект не найден."},
    )


@app.get("/api/projects")
def projects_list_endpoint(x_workspace: str | None = Header(default=None)) -> dict[str, Any]:
    workspace = _workspace(x_workspace)
    records = _project_store().list(workspace)
    return {"ok": True, "projects": [_stored_payload(record) for record in records]}


@app.get("/api/projects/{project_id}")
def project_get_endpoint(
    project_id: str,
    x_workspace: str | None = Header(default=None),
) -> dict[str, Any]:
    workspace = _workspace(x_workspace)
    record = _project_store().get(workspace, project_id)
    if record is None:
        raise _not_found()
    return {"ok": True, "project": _stored_payload(record)}


@app.put("/api/projects/{project_id}")
def project_put_endpoint(
    project_id: str,
    body: ProjectBody,
    x_workspace: str | None = Header(default=None),
) -> dict[str, Any]:
    if project_id != body.project.id:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "project_id_mismatch",
                "message": "ID в URL не совпадает с project.id.",
            },
        )
    try:
        workspace = _workspace(x_workspace)
        record = _project_store().put(workspace, body.project)
    except ProjectConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "error": "project_conflict",
                "message": "На сервере есть более свежая версия проекта.",
                "project": _stored_payload(exc.current),
            },
        ) from None
    except ProjectNotFoundError:
        raise _not_found() from None
    return {"ok": True, "project": _stored_payload(record)}


@app.delete("/api/projects/{project_id}")
def project_delete_endpoint(
    project_id: str,
    x_workspace: str | None = Header(default=None),
) -> dict[str, Any]:
    workspace = _workspace(x_workspace)
    if not _project_store().delete(workspace, project_id):
        raise _not_found()
    return {"ok": True}


@app.post("/api/projects/{project_id}/share")
def project_share_endpoint(
    project_id: str,
    x_workspace: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        workspace = _workspace(x_workspace)
        token = _project_store().share(workspace, project_id)
    except ProjectNotFoundError:
        raise _not_found() from None
    return {"ok": True, "token": token}


@app.get("/api/shared/{token}")
def project_shared_endpoint(token: str) -> dict[str, Any]:
    record = _project_store().get_shared(token)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "error": "share_not_found", "message": "Share-ссылка не найдена."},
        )
    project = record.project
    report = _report_for(project)
    if project.circuits:
        report = {**report, "sld": _sld_preview(project, report)}
    return {"ok": True, "project": _stored_payload(record), "report": report}


def _report_for(project: Project) -> dict[str, Any]:
    pack = _pack_or_400(project.norm_pack)
    with _heavy_operation():
        return _catch_project_error(  # type: ignore[no-any-return]
            build_project_report, project, data_pack=pack,
        )


def _sld_preview(project: Project, report: dict[str, Any]) -> dict[str, Any]:
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


@app.post("/api/project-validate")
def project_validate_endpoint(body: ProjectBody) -> dict[str, dict[str, Any]]:
    """Migrate/validate a project without running sizing, normcheck, or export."""
    return {"project": project_payload(body.project)}


@app.post("/api/normcheck")
def normcheck_endpoint(body: ProjectBody, request: Request) -> dict[str, Any]:
    """Run deterministic R01-R10 over fresh server-side board calculations (docs/14)."""
    pack = _pack_or_400(body.project.norm_pack)
    with _heavy_operation():
        report = _catch_project_error(build_normcheck_report, body.project, pack)
    cfg = get_config()
    if cfg.llm_available:
        _admit_llm(request)
        try:
            narrative = narrate_findings(
                report.findings, report.summary, _client(), model=cfg.model_fast,
            )
            report = report.model_copy(update={"narrative": narrative})
        except (LlmConfigError, LlmError):
            pass
    return report.model_dump()  # type: ignore[no-any-return]


@app.post("/api/import-schedule")
async def import_schedule_endpoint(
    request: Request,
    file: UploadFile = File(...),
    mapping: str | None = Form(default=None),
    confirmed: bool = Form(default=False),
    norm_pack: str | None = Form(default=None),
) -> dict[str, Any]:
    """Parse XLSX/CSV, expose review mapping/assumptions, and run deterministic R11."""
    filename = file.filename or "schedule"
    data = await file.read(MAX_IMPORT_BYTES + 1)
    manual_mapping: dict[str, object] | None = None
    if mapping is not None:
        try:
            parsed_mapping = json.loads(mapping)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_mapping_json", "message": "Mapping должен быть JSON-объектом."},
            ) from None
        if not isinstance(parsed_mapping, dict):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_mapping_json", "message": "Mapping должен быть JSON-объектом."},
            )
        manual_mapping = parsed_mapping
    pack = _pack_or_400(norm_pack)
    cfg = get_config()
    if cfg.llm_available and manual_mapping is None:
        _admit_llm(request)
    try:
        with _heavy_operation():
            result = import_schedule(
                filename=filename,
                data=data,
                pack=pack,
                manual_mapping=manual_mapping,
                confirmed=confirmed,
                llm_client=_client() if cfg.llm_available and manual_mapping is None else None,
                llm_model=cfg.model_fast if cfg.llm_available else None,
            )
    except ScheduleImportError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from None
    except DataPackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return result.model_dump(mode="json")


def _safe_filename(project: Project) -> str:
    base = str(project.board_ref or project.name or "board")
    return re.sub(r"[^\w.-]+", "_", base).strip("_") or "board"


@app.post("/api/project-sld")
def project_sld_endpoint(body: ProjectBody) -> dict[str, Any]:
    """Single-line diagram preview: first-sheet SVG + sheet count (docs/13)."""
    return _sld_preview(body.project, _report_for(body.project))


@app.post("/api/project-export")
def project_export_endpoint(body: ProjectBody) -> Response:
    """Full document package as a downloadable zip (docs/13)."""
    pack = _pack_or_400(body.project.norm_pack)
    with _heavy_operation():
        data: bytes = _catch_project_error(build_bundle, body.project, data_pack=pack)
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
