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
from dataclasses import asdict
from datetime import timezone
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
from .norms.parse import NormDocument, NormSection, source_href
from .norms.search import search_norms
from .norms.store import DocumentRecord, NormStore, open_store
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


def _optional_llm_client(request: Request) -> OpenRouterClient | None:
    """Non-raising admission check for endpoints where the LLM only adds an optional
    extra (a narrative, a rerank, a smarter header mapping) on top of an ALREADY complete
    deterministic result. An admission rejection here (docs/19, e.g. a fail-closed
    llm_admission_mode="disabled" with a key present) must degrade to the same no-LLM
    behaviour these endpoints already have, not take down the deterministic core."""
    if admission.admit_llm(request, get_config()) is not None:
        return None
    return _client()


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
            # The in-memory override is only injected by tests in-process. Reporting it as
            # available lets the real browser exercise the same sync/share paths as Neon;
            # normal application processes can only reach this branch via DATABASE_URL.
            "project_store": "neon" if _PROJECT_STORE_OVERRIDE is not None or cfg.db_available else "local",
            # Contract for the frontend (docs/19): a missing field means an old server that
            # cannot be assumed fail-closed or fail-open either way. No counters/telemetry here.
            "llm_admission": cfg.llm_admission_mode}


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
        attachments=body.attachments,
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
    """Run deterministic R01-R10 over fresh server-side board calculations (docs/14).

    The LLM narrative is optional garnish on an already-complete deterministic report:
    an admission rejection (docs/19) must not take the whole endpoint down with it — it
    just means the response comes back without a narrative, same as no key configured.
    """
    pack = _pack_or_400(body.project.norm_pack)
    with _heavy_operation():
        report = _catch_project_error(build_normcheck_report, body.project, pack)
    cfg = get_config()
    if cfg.llm_available:
        client = _optional_llm_client(request)
        if client is not None:
            try:
                narrative = narrate_findings(
                    report.findings, report.summary, client, model=cfg.model_fast,
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
    # LLM header mapping is a best-effort merge over the heuristic mapping (docs/14 §8);
    # an admission rejection degrades to the same heuristic-only path as no key configured,
    # rather than 503-ing an import the deterministic parser already handled.
    llm_client: OpenRouterClient | None = None
    if cfg.llm_available and manual_mapping is None:
        llm_client = _optional_llm_client(request)
    try:
        with _heavy_operation():
            result = import_schedule(
                filename=filename,
                data=data,
                pack=pack,
                manual_mapping=manual_mapping,
                confirmed=confirmed,
                llm_client=llm_client,
                llm_model=cfg.model_fast if llm_client is not None else None,
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


# --- norm library (docs/20 §7): read-only. Ingest stays CLI/offline, by spec. ---

_NORM_STORE_OVERRIDE: NormStore | None = None


def _norm_store() -> NormStore:
    """Unlike `/api/projects`, the norm library must NEVER 503 for a missing DATABASE_URL:
    docs/20 §3 makes the offline JSON backend a supported mode, not a test double."""
    if _NORM_STORE_OVERRIDE is not None:
        return _NORM_STORE_OVERRIDE
    return open_store()


def _norm_document_payload(record: DocumentRecord) -> dict[str, Any]:
    """Document header for every surface that shows its text.

    `status` and, for a repealed act, the verbatim `status_note` travel with EVERY response
    (docs/20 §8.4) — the badge is worthless if it is attached only to search results.
    """
    doc = record.document
    return {
        "id": doc.id,
        "title": doc.title,
        "kind": record.kind,
        "discipline": record.discipline,
        "status": doc.status,
        "status_note": doc.status_note,
        "sections": record.section_count,
        "source_url": doc.source_url,
        "fetched_at": record.fetched_at.astimezone(timezone.utc).isoformat(),
    }


def _norm_section_payload(doc: NormDocument, section: NormSection) -> dict[str, Any]:
    """A section is only ever exposed with its citation anchor (docs/20 §8.1).

    `source_url` is built by `source_href`, which refuses to invent a fragment — never by
    string concatenation.
    """
    return {
        "section_id": section.id,
        "doc_id": section.doc_id,
        "anchor": section.anchor,
        "ordinal": section.ordinal,
        "breadcrumb": section.breadcrumb,
        "heading": section.heading,
        "body": section.body,
        "has_table": section.has_table,
        "source_url": source_href(doc.source_url, section.anchor),
    }


def _norm_abstention(doc_id: str) -> HTTPException:
    """docs/20 §8.2: no such act in the corpus ⇒ say so and list what IS there.

    Guessing a similar document or paraphrasing a norm from model memory is forbidden; the
    honest, typed refusal is the product behaviour.
    """
    available = [
        {"id": rec.document.id, "title": rec.document.title, "status": rec.document.status}
        for rec in _norm_store().list_documents()
    ]
    return HTTPException(
        status_code=404,
        detail={
            "ok": False,
            "error": "norm_document_not_found",
            "message": f"Документа {doc_id} нет в нормативной библиотеке.",
            "available": available,
        },
    )


@app.get("/api/norms")
def norms_list_endpoint() -> dict[str, Any]:
    """Corpus listing: id, title, status, section count."""
    records = _norm_store().list_documents()
    return {"ok": True, "documents": [_norm_document_payload(r) for r in records]}


# Структура документа: у adilet заголовки разделов/глав/параграфов приходят отдельными
# секциями без тела, а пункты — секциями без заголовка. Оглавление строится по ПЕРВЫМ, а не по
# каждому пункту: иначе получается стена из 7791 кода вида z1938 вместо читаемого содержания.
_OUTLINE_DEPTH = (
    (re.compile(r"^Раздел\s+\d+"), 0),
    (re.compile(r"^Глава\s+\d+"), 1),
    (re.compile(r"^Параграф\s+\d+"), 2),
    (re.compile(r"^Приложение\s+\d+"), 0),
)
NORMS_PAGE_SIZE = 60


def _outline_depth(text: str) -> int | None:
    for pattern, depth in _OUTLINE_DEPTH:
        if pattern.match(text):
            return depth
    return None


def _reading_payload(doc: NormDocument, section: NormSection) -> dict[str, Any]:
    """Одна единица чтения: либо заголовок, либо пункт с текстом."""
    payload = _norm_section_payload(doc, section)
    payload["kind"] = "heading" if section.heading and not section.body.strip() else "clause"
    payload["depth"] = _outline_depth(section.heading or "") if section.heading else None
    return payload


@app.get("/api/norms/{doc_id}")
def norms_document_endpoint(
    doc_id: str,
    anchor: str | None = None,
    offset: int = 0,
    limit: int = NORMS_PAGE_SIZE,
) -> dict[str, Any]:
    """Оглавление документа + страница сплошного текста для чтения.

    Читалка показывает текст ПОДРЯД, как в первоисточнике, а не по одному пункту: документ
    читают, а не выбирают из справочника. `anchor` открывает страницу, на которой этот пункт
    лежит, — так работают и переход из поиска, и переход из цитаты расчёта.
    """
    store = _norm_store()
    record = store.get_document(doc_id)
    if record is None:
        raise _norm_abstention(doc_id)
    doc = record.document
    limit = max(1, min(limit, 300))
    view = store.reading_view(doc_id, anchor=anchor, offset=offset, limit=limit)
    total, offset, window = view.total, view.offset, view.page

    outline = [
        {"anchor": s.anchor, "text": s.heading, "depth": _outline_depth(s.heading or "") or 0}
        for s in view.headings
        if s.heading and _outline_depth(s.heading) is not None
    ]

    return {
        "ok": True,
        "document": _norm_document_payload(record),
        "outline": outline,
        "page": {
            "sections": [_reading_payload(doc, s) for s in window],
            "offset": offset,
            "limit": limit,
            "total": total,
            "prev_offset": max(0, offset - limit) if offset > 0 else None,
            "next_offset": offset + limit if offset + limit < total else None,
            "focus_anchor": anchor,
        },
    }


@app.get("/api/norms/{doc_id}/sections/{anchor}")
def norms_section_endpoint(doc_id: str, anchor: str) -> dict[str, Any]:
    """One section plus its neighbours, for the reader's prev/next navigation."""
    store = _norm_store()
    record = store.get_document(doc_id)
    if record is None:
        raise _norm_abstention(doc_id)
    sections = store.list_sections(doc_id)
    index = next((i for i, s in enumerate(sections) if s.anchor == anchor), None)
    if index is None:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "norm_section_not_found",
                "message": f"В документе {doc_id} нет пункта с якорем {anchor}.",
                "document": _norm_document_payload(record),
            },
        )
    doc = record.document

    def neighbour(offset: int) -> dict[str, Any] | None:
        pos = index + offset
        if pos < 0 or pos >= len(sections):
            return None
        near = sections[pos]
        return {"anchor": near.anchor, "heading": near.heading, "breadcrumb": near.breadcrumb}

    return {
        "ok": True,
        "document": _norm_document_payload(record),
        "section": _norm_section_payload(doc, sections[index]),
        "prev": neighbour(-1),
        "next": neighbour(1),
    }


class NormSearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    doc_ids: Optional[list[str]] = Field(default=None, max_length=16)
    limit: int = Field(default=20, ge=1, le=100)
    include_repealed: bool = False


@app.post("/api/norms/search")
def norms_search_endpoint(body: NormSearchBody, request: Request) -> dict[str, Any]:
    """Lexical search over the corpus (docs/20 §6).

    Sections of repealed documents stay hidden unless `include_repealed` is set. The LLM
    rerank only engages behind `NORMS_LLM_RERANK=1` with a key present; it sorts and nothing
    else, and an invalid response leaves the lexical order in place.
    """
    cfg = get_config()
    client: OpenRouterClient | None = None
    if cfg.norms_llm_rerank and cfg.llm_available:
        # Rerank is an optional reordering of an already-complete lexical result (docs/20
        # §6): an admission rejection just leaves the deterministic lexical order in place,
        # exactly like an invalid rerank response already does — never a 503 for a search.
        client = _optional_llm_client(request)
    with _heavy_operation():
        hits = search_norms(
            _norm_store(),
            body.query,
            doc_ids=list(body.doc_ids) if body.doc_ids else None,
            limit=body.limit,
            include_repealed=body.include_repealed,
            client=client,
            model=cfg.model_fast,
        )
    return {
        "ok": True,
        "rerank": client is not None,
        "results": [asdict(hit) for hit in hits],
    }


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
