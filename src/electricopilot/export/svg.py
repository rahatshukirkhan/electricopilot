"""Render a Drawing to a deterministic SVG string (docs/13).

Deterministic on purpose: no random ids, fixed coordinate precision, stable element order —
so a golden snapshot test is meaningful. Y is used directly (SVG and our authoring space are
both Y-down). Also used for the Studio single-line preview.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from .primitives import Circle, Drawing, Line, Polyline, Rect, Text

# Layer → stroke colour for on-screen / print SVG (design-v2-spec §3: server SLD outlines are
# navy; TEXT is unused in practice — Text primitives carry their own .color, see primitives.py —
# but kept consistent with --ink in case that ever changes).
_LAYER_STROKE = {
    "FRAME": "#24407A",    # --navy
    "BUS": "#24407A",      # --navy
    "WIRES": "#8B93A5",    # --faint (fallback only; circuit wires are normally status-coloured)
    "SYMBOLS": "#24407A",  # --navy
    "TEXT": "#1C2536",     # --ink
}


def _n(v: float) -> str:
    """Fixed 3-decimal formatting, trailing zeros stripped → stable, compact, deterministic."""
    return f"{v:.3f}".rstrip("0").rstrip(".")


def render_svg(dwg: Drawing) -> str:
    w, h = _n(dwg.width_mm), _n(dwg.height_mm)
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" '
        f'viewBox="0 0 {w} {h}" fill="none">',
        '<rect x="0" y="0" '
        f'width="{w}" height="{h}" fill="#ffffff"/>',
    ]
    for p in dwg.primitives:
        out.append(_render_one(p))
    out.append("</svg>")
    return "\n".join(out)


def _render_one(p: object) -> str:
    if isinstance(p, Line):
        c = p.color or _LAYER_STROKE[p.layer]
        return (f'<line x1="{_n(p.x1)}" y1="{_n(p.y1)}" x2="{_n(p.x2)}" y2="{_n(p.y2)}" '
                f'stroke="{c}" stroke-width="{_n(p.width)}"/>')
    if isinstance(p, Rect):
        c = _LAYER_STROKE[p.layer]
        return (f'<rect x="{_n(p.x)}" y="{_n(p.y)}" width="{_n(p.w)}" height="{_n(p.h)}" '
                f'stroke="{c}" stroke-width="{_n(p.width)}" fill="none"/>')
    if isinstance(p, Circle):
        c = _LAYER_STROKE[p.layer]
        return (f'<circle cx="{_n(p.cx)}" cy="{_n(p.cy)}" r="{_n(p.r)}" '
                f'stroke="{c}" stroke-width="{_n(p.width)}" fill="none"/>')
    if isinstance(p, Polyline):
        c = p.color or _LAYER_STROKE[p.layer]
        pts = " ".join(f"{_n(x)},{_n(y)}" for x, y in p.points)
        tag = "polygon" if p.closed else "polyline"
        fill = c if p.closed else "none"
        return f'<{tag} points="{pts}" stroke="{c}" stroke-width="{_n(p.width)}" fill="{fill}"/>'
    if isinstance(p, Text):
        # Anchor literal values ("start"/"middle"/"end") are already the SVG text-anchor values.
        rot = "" if p.rotation == 0 else f' transform="rotate({_n(-p.rotation)} {_n(p.x)} {_n(p.y)})"'
        return (f'<text x="{_n(p.x)}" y="{_n(p.y)}" font-size="{_n(p.height)}" '
                f'font-family="Helvetica, Arial, sans-serif" fill="{p.color}" '
                f'text-anchor="{p.anchor}"{rot}>{escape(p.text)}</text>')
    raise TypeError(f"unknown primitive: {type(p).__name__}")
