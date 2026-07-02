"""Schematic condition symbols (simplified per ГОСТ 2.755) as pure geometry factories.

Each function returns a list[Primitive] positioned around a connection point on a vertical
circuit wire. Text labels (In, curve, section…) are NOT baked in here — sld.py adds them, so
symbols stay pure geometry (docs/13). Y is down (see primitives.py).
"""
from __future__ import annotations

from .primitives import Circle, Line, Polyline, Primitive, Rect, Text


def breaker(x: float, y: float, w: float = 7.0, h: float = 9.0) -> list[Primitive]:
    """Automatic circuit breaker (QF). Box on the wire with a diagonal 'switch' stroke."""
    left, right = x - w / 2, x + w / 2
    top, bot = y - h / 2, y + h / 2
    return [
        Rect(left, top, w, h, layer="SYMBOLS"),
        Line(x, top, x, bot, layer="SYMBOLS"),          # contact stem
        Line(left + 1.0, bot - 1.5, right - 1.0, top + 1.5, layer="SYMBOLS"),  # switch blade
    ]


def rcd(x: float, y: float, w: float = 9.0, h: float = 11.0) -> list[Primitive]:
    """RCD / RCBO (УЗО/АВДТ): breaker box + a small toroid circle for the differential sensor."""
    left, right = x - w / 2, x + w / 2
    top, bot = y - h / 2, y + h / 2
    return [
        Rect(left, top, w, h, layer="SYMBOLS"),
        Line(x, top, x, bot, layer="SYMBOLS"),
        Line(left + 1.2, bot - 1.8, right - 1.2, top + 1.8, layer="SYMBOLS"),
        Circle(x, bot - 2.2, 1.6, layer="SYMBOLS"),     # residual-current toroid
    ]


def fuse(x: float, y: float, w: float = 5.0, h: float = 9.0) -> list[Primitive]:
    """gG fuse (предохранитель): rectangle with a longitudinal centre line (ГОСТ symbol)."""
    left = x - w / 2
    top, bot = y - h / 2, y + h / 2
    return [
        Rect(left, top, w, h, layer="SYMBOLS"),
        Line(x, top, x, bot, layer="SYMBOLS"),
    ]


def bus(x1: float, x2: float, y: float) -> list[Primitive]:
    """Horizontal distribution bus (шина) — a heavy line on the BUS layer."""
    return [Line(x1, y, x2, y, layer="BUS", width=1.2)]


def wire(x: float, y1: float, y2: float) -> list[Primitive]:
    """Vertical circuit conductor drop from the bus."""
    return [Line(x, y1, x, y2, layer="WIRES")]


def load_arrow(x: float, y: float, size: float = 4.0) -> list[Primitive]:
    """Downward load arrow (стрелка нагрузки) — filled-look triangle via a closed polyline."""
    return [
        Polyline(
            ((x - size / 2, y), (x + size / 2, y), (x, y + size)),
            layer="SYMBOLS", closed=True,
        )
    ]


def sheet_frame(width: float, height: float, margin: float = 10.0,
                title: str = "", subtitle: str = "") -> list[Primitive]:
    """Sheet border + a title-block stub (штамп-заглушка) in the bottom-right corner."""
    prims: list[Primitive] = [
        Rect(0, 0, width, height, layer="FRAME", width=0.7),                 # trim edge
        Rect(margin, margin, width - 2 * margin, height - 2 * margin, layer="FRAME", width=0.5),
    ]
    # Title block (stub): ГОСТ-style stamp is 185×55; keep it inside the inner frame.
    bw, bh = 185.0, 40.0
    bx = width - margin - bw
    by = height - margin - bh
    prims.append(Rect(bx, by, bw, bh, layer="FRAME", width=0.5))
    prims.append(Line(bx, by + 14, bx + bw, by + 14, layer="FRAME"))
    prims.append(Line(bx + 130, by, bx + 130, by + bh, layer="FRAME"))
    if title:
        prims.append(Text(bx + 4, by + 9, title, height=3.5, layer="TEXT", anchor="start"))
    if subtitle:
        prims.append(Text(bx + 4, by + 24, subtitle, height=2.5, layer="TEXT", anchor="start"))
    prims.append(Text(bx + 134, by + 9, "ElectriCopilot", height=3.0, layer="TEXT"))
    prims.append(Text(bx + 134, by + 24, "рекоменд. / не сертификация",
                      height=2.0, layer="TEXT"))
    return prims
