"""Bounded OpenRouter tool loop for the read-only board-level Copilot."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol

from ..data.loader import load_pack_by_name
from ..exceptions import LlmConfigError, LlmError
from ..llm.intake import intake_parse
from ..project import build_project_report
from ..project_contract import Project, validate_project
from .models import CopilotResponse, HistoryMessage, Proposal
from .tools import (
    TOOL_DEFINITIONS,
    CopilotToolError,
    check_reply_provenance,
    execute_tool,
    report_tool_payload,
)

SYSTEM = (
    "Ты — Copilot электрического щита. Не вычисляй инженерные числа и не вспоминай нормы. "
    "Для состояния, расчёта и проверок вызывай только доступные инструменты. Сервер не "
    "изменяет проект: изменения допустимы только через propose_changes. Перед add/edit собери "
    "полный SizingRequest. Не утверждай соответствие и напоминай о UNSIGNED_ADVISORY. "
    "При propose_changes включи краткий reply в content того же ответа."
)


class CopilotClient(Protocol):
    def complete_tools(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: float,
    ) -> dict[str, Any]: ...

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        json_schema: dict[str, Any] | None = None,
        max_output_tokens: int = 2048,
        timeout: float | None = None,
    ) -> str: ...


def _failure(model: str, error: str, reply: str, *, incomplete: bool = True) -> CopilotResponse:
    return CopilotResponse(
        ok=False,
        reply=reply,
        model=model,
        incomplete=incomplete,
        error=error,
    )


def _assistant_message(turn: dict[str, Any]) -> dict[str, Any]:
    calls = []
    for call in turn.get("tool_calls", []):
        calls.append({
            "id": call["id"],
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": json.dumps(call["arguments"], ensure_ascii=False, sort_keys=True),
            },
        })
    return {"role": "assistant", "content": turn.get("content"), "tool_calls": calls}


def run_copilot(
    project: Project,
    message: str,
    history: list[HistoryMessage],
    *,
    client: CopilotClient,
    model: str,
    parse_model: str,
    max_iterations: int = 6,
    time_budget_seconds: float = 45.0,
    clock: Callable[[], float] = time.monotonic,
) -> CopilotResponse:
    """Run a bounded tool loop and return a proposal without mutating the input project."""
    started = clock()
    canonical = validate_project(project)
    pack = load_pack_by_name(canonical.norm_pack)
    baseline = report_tool_payload(build_project_report(canonical, data_pack=pack))
    evidence: list[Any] = [baseline]
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}]
    messages.extend(item.model_dump() for item in history)
    messages.append({"role": "user", "content": message})
    proposal: Proposal | None = None

    def parse(text: str, remaining: float) -> Any:
        return intake_parse(text, client, model=parse_model, timeout=remaining)

    for _iteration in range(max_iterations):
        remaining = time_budget_seconds - (clock() - started)
        if remaining <= 0:
            return _failure(model, "timeout", "Время Copilot истекло; проект не изменён.")
        try:
            turn = client.complete_tools(
                model=model,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                timeout=remaining,
            )
        except Exception as exc:  # provider/client failures must not escape as HTTP 500
            return _failure(model, "model_error", f"Copilot не завершил запрос: {exc}")

        if clock() - started >= time_budget_seconds:
            return _failure(model, "timeout", "Время Copilot истекло; проект не изменён.")

        content = str(turn.get("content") or "").strip()
        calls = turn.get("tool_calls") or []
        if not isinstance(calls, list):
            return _failure(model, "model_error", "Copilot вернул некорректный tool_calls.")
        if len(calls) > 8:
            return _failure(model, "invalid_tool", "Copilot запросил слишком много инструментов.")
        if not calls:
            reply = content or "Copilot не сформировал ответ; проект не изменён."
            provenance_ok, unverified = check_reply_provenance(reply, evidence)
            return CopilotResponse(
                ok=True,
                reply=reply,
                proposal=proposal,
                model=model,
                provenance_ok=provenance_ok,
                unverified_numbers=unverified,
            )

        messages.append(_assistant_message(turn))
        for call in calls:
            try:
                call_id = str(call["id"])
                name = str(call["name"])
                if not call_id or not name:
                    raise CopilotToolError("invalid_tool", "tool call requires id and name")
                arguments = call["arguments"]
                if not isinstance(arguments, dict):
                    raise CopilotToolError("invalid_tool", "tool arguments must be an object")
                remaining = time_budget_seconds - (clock() - started)
                if remaining <= 0:
                    return _failure(model, "timeout", "Время Copilot истекло; проект не изменён.")
                payload, produced, tool_evidence = execute_tool(
                    name,
                    arguments,
                    project=canonical,
                    pack=pack,
                    parse_circuit=parse,
                    remaining_seconds=remaining,
                )
                if clock() - started >= time_budget_seconds:
                    return _failure(
                        model, "timeout", "Время Copilot истекло; проект не изменён.",
                    )
            except (CopilotToolError, LlmConfigError, LlmError) as exc:
                code = exc.code if isinstance(exc, CopilotToolError) else "model_error"
                return _failure(model, code, f"Copilot остановлен: {exc}")
            evidence.extend(tool_evidence)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            })
            if produced is not None:
                proposal = produced
                reply = content or "Предложение подготовлено; проект изменится только после подтверждения."
                provenance_ok, unverified = check_reply_provenance(reply, evidence)
                return CopilotResponse(
                    ok=True,
                    reply=reply,
                    proposal=proposal,
                    model=model,
                    provenance_ok=provenance_ok,
                    unverified_numbers=unverified,
                )

    return _failure(model, "iteration_limit", "Copilot достиг лимита итераций; проект не изменён.")
