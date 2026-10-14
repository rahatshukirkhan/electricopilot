"""OpenRouter (OpenAI-compatible) client for Gemini (docs/05 §5.1).

Robust against reasoning models: uses a generous token budget and treats an empty
`content` as an error rather than silently returning "". Unknown model slugs raise
LlmConfigError (D10) instead of falling back silently.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..config import Config, get_config
from ..exceptions import LlmConfigError, LlmError

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_lenient(text: str) -> Any:
    """Parse JSON possibly wrapped in ``` fences or surrounded by prose."""
    cleaned = _FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if 0 <= start < end:
            return json.loads(cleaned[start:end + 1])
        raise


class OpenRouterClient:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or get_config()

    @property
    def available(self) -> bool:
        return self.config.llm_available

    def complete(
        self, *, model: str, system: str, user: str,
        json_schema: dict[str, Any] | None = None, max_output_tokens: int = 2048,
        timeout: float | None = None,
    ) -> str:
        cfg = self.config
        if not cfg.llm_available:
            raise LlmConfigError("OPENROUTER_API_KEY is not set; live LLM unavailable")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_output_tokens,
        }
        if json_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {cfg.openrouter_api_key}",
            "X-Title": cfg.app_title,
            "HTTP-Referer": cfg.http_referer,
        }
        url = f"{cfg.openrouter_base_url}/chat/completions"
        last_exc: Exception | None = None
        request_timeout = float(timeout if timeout is not None else cfg.llm_timeout)
        for attempt in range(2):
            try:
                resp = httpx.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=max(0.1, request_timeout / 2),
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                continue
            if resp.status_code == 200:
                data = resp.json()
                choice = data["choices"][0]
                content = choice.get("message", {}).get("content")
                if not content:
                    raise LlmError(
                        f"empty content from {model} (finish_reason="
                        f"{choice.get('finish_reason')}); increase max_output_tokens"
                    )
                return str(content)
            body = resp.text[:300]
            if resp.status_code in (400, 404) and ("model" in body.lower() or resp.status_code == 404):
                raise LlmConfigError(f"model '{model}' rejected by OpenRouter ({resp.status_code}): {body}")
            if resp.status_code < 500:
                raise LlmError(f"OpenRouter {resp.status_code}: {body}")
            last_exc = LlmError(f"OpenRouter {resp.status_code}: {body}")
        raise LlmError(f"OpenRouter request failed after retries: {last_exc}")

    def complete_tools(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        timeout: float,
    ) -> dict[str, Any]:
        """Return one normalized OpenAI-compatible function-calling turn."""
        cfg = self.config
        if not cfg.llm_available:
            raise LlmConfigError("OPENROUTER_API_KEY is not set; live LLM unavailable")
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            # Reasoning models (Gemini 3.x) burn output budget on hidden thinking before
            # emitting tool calls; 2048 produced empty finish_reason=length turns on real
            # apartment briefs. Generous cap + low effort keeps the orchestration loop fast.
            "max_tokens": 8192,
            "reasoning": {"effort": "low"},
        }
        headers = {
            "Authorization": f"Bearer {cfg.openrouter_api_key}",
            "X-Title": cfg.app_title,
            "HTTP-Referer": cfg.http_referer,
        }
        url = f"{cfg.openrouter_base_url}/chat/completions"
        last_exc: Exception | None = None
        # The first attempt gets most of the wall budget (reasoning + a full-board proposal
        # is slow); the retry gets the remainder so both attempts stay inside `timeout`.
        attempt_timeouts = (max(0.1, timeout * 0.7), max(0.1, timeout * 0.3))
        for attempt_timeout in attempt_timeouts:
            try:
                response = httpx.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=attempt_timeout,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                continue
            if response.status_code == 200:
                choice = response.json()["choices"][0]
                message = choice["message"]
                normalized_calls: list[dict[str, Any]] = []
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError as exc:
                        raise LlmError("tool arguments are not valid JSON") from exc
                    if not isinstance(arguments, dict):
                        raise LlmError("tool arguments must be a JSON object")
                    normalized_calls.append({
                        "id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": arguments,
                    })
                content = message.get("content")
                if not content and not normalized_calls:
                    # A thinking-only turn (all budget spent on reasoning) is transient:
                    # retry once before surfacing a diagnosable error.
                    last_exc = LlmError(
                        "empty tool-calling response "
                        f"(finish_reason={choice.get('finish_reason')})"
                    )
                    continue
                return {"content": content, "tool_calls": normalized_calls}
            body = response.text[:300]
            if response.status_code in (400, 404) and (
                "model" in body.lower() or response.status_code == 404
            ):
                raise LlmConfigError(
                    f"model '{model}' rejected by OpenRouter ({response.status_code}): {body}"
                )
            if response.status_code < 500:
                raise LlmError(f"OpenRouter {response.status_code}: {body}")
            last_exc = LlmError(f"OpenRouter {response.status_code}: {body}")
        raise LlmError(f"OpenRouter tool request failed after retries: {last_exc}")
