#!/usr/bin/env python3
"""Discover exact OpenRouter model slugs for Gemini (run once a key is set).

Usage:
    uv run python scripts/list_models.py            # lists gemini* slugs
    uv run python scripts/list_models.py --all      # dumps every slug

Reads OPENROUTER_API_KEY / OPENROUTER_BASE_URL from the environment (.env).
Prints nothing sensitive; helps you confirm ELECTRICOPILOT_MODEL_* values.
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    if not key:
        print("OPENROUTER_API_KEY is not set — add it to .env first.", file=sys.stderr)
        return 2
    show_all = "--all" in sys.argv
    resp = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    resp.raise_for_status()
    models = resp.json().get("data", [])
    slugs = sorted(m["id"] for m in models)
    for slug in slugs:
        if show_all or "gemini" in slug.lower():
            print(slug)
    print(f"\n{len(slugs)} models total; filter='{'all' if show_all else 'gemini'}'", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
