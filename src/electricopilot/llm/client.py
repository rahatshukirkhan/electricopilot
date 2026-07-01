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
        for attempt in range(2):
            try:
                resp = httpx.post(url, headers=headers, json=payload, timeout=cfg.llm_timeout)
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
