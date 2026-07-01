"""LLM layer (Gemini via OpenRouter). Optional: fallbacks require no key (docs/05)."""
from .client import OpenRouterClient, parse_json_lenient
from .explain import explain_render, explain_render_template
from .intake import intake_parse
from .verify import verify_deterministic_check, verify_review

__all__ = [
    "OpenRouterClient", "parse_json_lenient",
    "intake_parse", "explain_render", "explain_render_template",
    "verify_review", "verify_deterministic_check",
]
