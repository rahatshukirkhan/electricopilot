"""Bounded OpenRouter tool loop for the read-only board-level Copilot."""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol

from ..data.loader import load_data_pack
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
    "Ты — Copilot электрического щита: помощник электрика при планировании цепей квартиры "
    "или дома.\n"
    "\n"
    "Железные правила:\n"
    "- Не вычисляй инженерные числа (сечение, номинал аппарата, ΔU) и не вспоминай таблицы "
    "норм по памяти — это делают только инструменты. Числа в финальном ответе допустимы "
    "только из результатов инструментов.\n"
    "- Сервер ничего не меняет: изменения возможны только через propose_changes, применяет "
    "их пользователь. Перед add/edit собери полный SizingRequest: обязательны "
    "installation.method и installation.length_m; method, material, insulation и "
    "ambient_temp_c бери из supply щита, если пользователь не сказал иное. Если инструмент "
    "вернул ошибку валидации — исправь аргументы и повтори вызов.\n"
    "- Не утверждай соответствие нормам; напоминай, что итог — UNSIGNED_ADVISORY и требует "
    "подписи инженера.\n"
    "\n"
    "Как работать с запросом:\n"
    "1. Сначала вызови get_board_state: пойми ввод (фазы, напряжение), способ прокладки, "
    "материал и типичные параметры существующих цепей — новые цепи наследуют эти условия, "
    "если пользователь не сказал иное.\n"
    "2. Если пользователь описал квартиру, зону или технику (кухня, ТВ-зона, бойлер, "
    "санузел, кондиционер) — составь план цепей по практике: стационарная техника примерно "
    "от 2 кВт (варочная, духовка, посудомойка, стиральная, бойлер, кондиционер) — отдельная "
    "линия на каждую; розеточные группы по помещениям, кухонные розетки отдельно от комнат; "
    "освещение — отдельными группами, не смешивай с розетками; влажные зоны (санузел, "
    "бойлер, стиральная, кухня, улица) — meta.rcd.present=true с ma=30; при трёхфазном "
    "вводе распределяй однофазные линии по L1/L2/L3 равномерно (meta.phase); ref давай "
    "говорящие («Духовка», «Розетки кухни»). По анкете квартиры (комнаты, санузлы, техника) "
    "для пустого щита предложи полный набор цепей одним proposal.\n"
    "3. Мощности техники — входные данные: бери их из сообщения пользователя. Если мощность "
    "или длина линии неизвестна — либо задай ОДИН короткий вопрос сразу по всем недостающим "
    "пунктам, либо прими типовое допущение, явно перечисли допущения в ответе и попроси "
    "подтвердить.\n"
    "4. Собери ВСЕ операции в один вызов propose_changes; отдельную цепь можно предварительно "
    "проверить через compute, свободное описание одной цепи — разобрать через parse_circuit.\n"
    "5. Если приложено фото или план (в том числе PDF): перечисли, какие помещения и "
    "технику ты на нём распознал, и составь план по п.2. Длины трасс по картинке не "
    "измеряй — это допущения, назови их и попроси подтвердить.\n"
    "6. При propose_changes включи краткий reply в content того же ответа: что добавлено или "
    "изменено и какие допущения приняты."
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


def _user_message(message: str, attachments: list[str]) -> dict[str, Any]:
    """Plain text, or OpenAI-style multimodal parts when a floor plan is attached.

    Raster photos become image_url parts; a PDF plan becomes a file part — Gemini via
    OpenRouter reads both natively, no external parser plugin involved.
    """
    if not attachments:
        return {"role": "user", "content": message}
    parts: list[dict[str, Any]] = [{"type": "text", "text": message}]
    for index, url in enumerate(attachments, start=1):
        if url.startswith("data:application/pdf"):
            parts.append({
                "type": "file",
                "file": {"filename": f"plan-{index}.pdf", "file_data": url},
            })
        else:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": "user", "content": parts}


def run_copilot(
    project: Project,
    message: str,
    history: list[HistoryMessage],
    *,
    client: CopilotClient,
    model: str,
    parse_model: str,
    attachments: list[str] | None = None,
    max_iterations: int = 6,
    time_budget_seconds: float = 45.0,
    clock: Callable[[], float] = time.monotonic,
) -> CopilotResponse:
    """Run a bounded tool loop and return a proposal without mutating the input project."""
    started = clock()
    canonical = validate_project(project)
    pack = load_data_pack(canonical.norm_pack)
    baseline = report_tool_payload(build_project_report(canonical, data_pack=pack))
    evidence: list[Any] = [baseline]
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}]
    messages.extend(item.model_dump() for item in history)
    messages.append(_user_message(message, attachments or []))
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
            call_id = str(call.get("id") or "")
            name = str(call.get("name") or "")
            arguments = call.get("arguments")
            if not call_id or not name or not isinstance(arguments, dict):
                return _failure(model, "invalid_tool", "Copilot вернул некорректный вызов инструмента.")
            remaining = time_budget_seconds - (clock() - started)
            if remaining <= 0:
                return _failure(model, "timeout", "Время Copilot истекло; проект не изменён.")
            try:
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
            except (LlmConfigError, LlmError) as exc:
                return _failure(model, "model_error", f"Copilot остановлен: {exc}")
            except CopilotToolError as exc:
                # A recoverable validation error (missing SizingRequest field, unknown
                # circuit id, bad ops) goes back to the model as a structured tool result
                # so it can correct itself; iterations and the time budget stay the caps.
                # Error text is NOT evidence — no numbers from it may enter the reply.
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json.dumps(
                        {"ok": False, "error": exc.code, "message": str(exc)},
                        ensure_ascii=False, sort_keys=True,
                    ),
                })
                continue
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
