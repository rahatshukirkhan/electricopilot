"""Vercel Python serverless entrypoint — exposes the Studio FastAPI app as `app` (ASGI).

Vercel's @vercel/python builder detects the `app` object. The engine package lives under
../src and is added to sys.path; its data pack ships via `includeFiles: src/**` (vercel.json).
Secrets (OPENROUTER_API_KEY) come from Vercel project env — never committed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from electricopilot.studio_api import app  # noqa: E402  (after sys.path setup)

__all__ = ["app"]
