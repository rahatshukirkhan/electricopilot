"""Deterministic IEC 60364 sizing engine. Owns every number; no LLM here."""
from .sizer import size

__all__ = ["size"]
