"""Resource admission policy for the public Studio API (docs/19).

The counters here deliberately protect one Python process only.  They are useful for
local development and tests, but are never presented as a distributed Vercel quota.
Production live LLM traffic is therefore fail-closed unless the owner explicitly
enables and operates a Vercel Firewall rule described in docs/19.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import logging
from threading import BoundedSemaphore, Lock
from time import monotonic

from fastapi import HTTPException, Request

from .config import Config

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdmissionRejection:
    status_code: int
    code: str
    message: str
    retry_after: int | None = None


def rejected(rejection: AdmissionRejection) -> HTTPException:
    headers = {"Retry-After": str(rejection.retry_after)} if rejection.retry_after else None
    return HTTPException(
        status_code=rejection.status_code,
        detail={"ok": False, "error": rejection.code, "message": rejection.message},
        headers=headers,
    )


class AdmissionController:
    """Bounded local admission.  No request body, project, or secret is retained."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._calls: dict[str, deque[float]] = {}
        self._global: deque[float] = deque()
        self._events: Counter[str] = Counter()
        self._gate: BoundedSemaphore | None = None
        self._gate_size: int | None = None

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()
            self._global.clear()
            self._events.clear()

    def _event(self, code: str) -> None:
        """Emit only a decision code; never include request-derived data."""
        self._events[code] += 1
        _LOG.info("admission_decision code=%s", code)

    def _trim(self, calls: deque[float], now: float, window: int) -> None:
        while calls and calls[0] <= now - window:
            calls.popleft()

    def admit_llm(self, request: Request, cfg: Config) -> AdmissionRejection | None:
        mode = getattr(cfg, "llm_admission_mode", "local")
        if mode == "disabled":
            self._event("llm_admission_disabled")
            return AdmissionRejection(
                503, "llm_admission_not_configured",
                "Живой ИИ временно отключён: защита публичного лимита ещё не подтверждена.",
            )
        if mode != "local":
            self._event("llm_admission_unknown_mode")
            return AdmissionRejection(503, "llm_admission_invalid", "Недопустимая политика доступа к ИИ.")

        forwarded = request.headers.get("x-forwarded-for", "")
        client = forwarded.split(",", 1)[0].strip() or (request.client.host if request.client else "unknown")
        now = monotonic()
        with self._lock:
            client_calls = self._calls.setdefault(client, deque())
            window = getattr(cfg, "llm_window_seconds", 60)
            client_limit = getattr(cfg, "llm_requests_per_window", 12)
            global_limit = getattr(cfg, "llm_global_requests_per_window", 24)
            self._trim(client_calls, now, window)
            self._trim(self._global, now, window)
            if len(client_calls) >= client_limit:
                self._event("llm_client_quota")
                return AdmissionRejection(
                    429, "llm_client_quota", "Превышен лимит запросов к ИИ. Повторите позже.",
                    window,
                )
            if len(self._global) >= global_limit:
                self._event("llm_global_budget")
                return AdmissionRejection(
                    503, "llm_global_budget", "Общий бюджет ИИ на этот интервал исчерпан. Повторите позже.",
                    window,
                )
            client_calls.append(now)
            self._global.append(now)
            self._event("llm_admitted")
        return None

    def acquire_heavy(self, cfg: Config) -> bool:
        limit = getattr(cfg, "max_concurrent_heavy_operations", 2)
        with self._lock:
            if self._gate is None or self._gate_size != limit:
                self._gate = BoundedSemaphore(limit)
                self._gate_size = limit
            gate = self._gate
        acquired = gate.acquire(blocking=False)
        self._event("heavy_admitted" if acquired else "heavy_concurrency")
        return acquired

    def release_heavy(self) -> None:
        assert self._gate is not None
        self._gate.release()

    def telemetry(self) -> dict[str, int]:
        """Count-only telemetry for a trusted metrics sink; never expose it publicly."""
        with self._lock:
            return dict(self._events)


admission = AdmissionController()
