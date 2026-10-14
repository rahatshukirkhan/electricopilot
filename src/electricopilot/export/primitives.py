"""Flat 2D geometry primitives — the SINGLE source of drawing geometry (docs/13).

Coordinates are millimetres in a Y-DOWN, origin-top-left sheet space (so layout code reads
naturally: increasing y goes downward, matching the plan's "сверху вниз" single-line layout).
The SVG renderer uses this directly; the DXF renderer flips Y (CAD is Y-up). Renderers are
dumb: they translate primitives 1:1 and never compute layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

# Drawing layers — mirror the DXF layer names (docs/12 §2.1).
Layer = Literal["FRAME", "BUS", "WIRES", "SYMBOLS", "TEXT"]
Anchor = Literal["start", "middle", "end"]


@dataclass(frozen=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    layer: Layer = "WIRES"
    width: float = 0.25  # mm, illustrative lineweight
    color: str | None = None  # hex override (e.g. status colour); None → layer colour


@dataclass(frozen=True)
class Polyline:
    points: tuple[tuple[float, float], ...]
    layer: Layer = "WIRES"
    width: float = 0.25
    closed: bool = False
    color: str | None = None


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float
    layer: Layer = "FRAME"
    width: float = 0.25


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float
    layer: Layer = "SYMBOLS"
    width: float = 0.25


@dataclass(frozen=True)
class Text:
    x: float
    y: float
    text: str
    height: float = 2.5  # mm cap height
    layer: Layer = "TEXT"
    anchor: Anchor = "start"
    rotation: float = 0.0  # degrees CCW
    color: str = "#1C2536"  # --ink (design-v2-spec §3); SVG stroke/fill; DXF uses layer color


Primitive = Union[Line, Polyline, Rect, Circle, Text]


@dataclass
class Drawing:
    """A single sheet's worth of primitives plus its page size (mm)."""

    width_mm: float
    height_mm: float
    primitives: list[Primitive] = field(default_factory=list)

    def add(self, *prims: Primitive) -> None:
        self.primitives.extend(prims)

    def extend(self, prims: list[Primitive]) -> None:
        self.primitives.extend(prims)
