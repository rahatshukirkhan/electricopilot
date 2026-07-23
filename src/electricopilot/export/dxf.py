"""Render a Drawing to DXF bytes via ezdxf (docs/13).

DXF is Y-up, our authoring space is Y-down, so every y is flipped (y_dxf = height − y). Layers
FRAME/BUS/WIRES/SYMBOLS/TEXT carry ACI colours. R2010 with UTF-8 keeps Cyrillic text intact in
free viewers (LibreCAD, ODA File Converter).
"""
from __future__ import annotations

import io
from datetime import datetime

import ezdxf
from ezdxf.document import (
    CONST_GUID,
    CONST_MARKER_STRING,
    CREATED_BY_EZDXF,
    WRITTEN_BY_EZDXF,
)
from ezdxf.enums import TextEntityAlignment
from ezdxf.lldxf.tagwriter import TagWriter
from ezdxf.tools.juliandate import juliandate

from .primitives import Circle, Drawing, Line, Polyline, Primitive, Rect, Text


def _rgb(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    if len(h) == 3:  # shorthand #rgb → #rrggbb (Text colours like "#556" use it)
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16) << 16) | (int(h[2:4], 16) << 8) | int(h[4:6], 16)

# Layer → ACI colour index (7=black/white, 8=dark gray, 9=light gray, 5=blue).
_LAYER_ACI = {"FRAME": 8, "BUS": 5, "WIRES": 9, "SYMBOLS": 7, "TEXT": 7}
_ALIGN = {"start": TextEntityAlignment.LEFT,
          "middle": TextEntityAlignment.CENTER,
          "end": TextEntityAlignment.RIGHT}
_FIXED_METADATA_DATE = juliandate(datetime(2000, 1, 1))


def _write_deterministic(doc: ezdxf.document.Drawing) -> bytes:
    """Serialize an ezdxf document with its generated metadata pinned.

    ``Drawing.write()`` regenerates timestamps, GUIDs, and an ezdxf writer marker on every
    call. Export through the same structured DXF model after its regular update pass, then
    replace only those non-geometric metadata fields before emitting the sections.
    """
    doc.commit_pending_changes()
    doc.classes.add_required_classes(doc.dxfversion)
    doc.update_all()
    for name in ("$TDCREATE", "$TDUCREATE", "$TDUPDATE", "$TDUUPDATE"):
        doc.header[name] = _FIXED_METADATA_DATE
    doc.header["$FINGERPRINTGUID"] = CONST_GUID
    doc.header["$VERSIONGUID"] = CONST_GUID
    metadata = doc.ezdxf_metadata()
    metadata[CREATED_BY_EZDXF] = CONST_MARKER_STRING
    metadata[WRITTEN_BY_EZDXF] = CONST_MARKER_STRING

    stream = io.StringIO()
    doc.export_sections(TagWriter(stream, write_handles=True, dxfversion=doc.dxfversion))
    return stream.getvalue().encode("utf-8")


def render_dxf(dwg: Drawing) -> bytes:
    doc = ezdxf.new("R2010", setup=True)
    for name, aci in _LAYER_ACI.items():
        doc.layers.add(name, color=aci)
    msp = doc.modelspace()
    h = dwg.height_mm

    def fy(y: float) -> float:
        return h - y  # flip to Y-up

    def _attribs(p: Primitive, layer: str) -> dict[str, object]:
        a: dict[str, object] = {"layer": layer}
        color = getattr(p, "color", None)
        if color:
            a["true_color"] = _rgb(color)
        return a

    for p in dwg.primitives:
        if isinstance(p, Line):
            msp.add_line((p.x1, fy(p.y1)), (p.x2, fy(p.y2)), dxfattribs=_attribs(p, p.layer))
        elif isinstance(p, Rect):
            pts = [(p.x, fy(p.y)), (p.x + p.w, fy(p.y)),
                   (p.x + p.w, fy(p.y + p.h)), (p.x, fy(p.y + p.h))]
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": p.layer})
        elif isinstance(p, Circle):
            msp.add_circle((p.cx, fy(p.cy)), p.r, dxfattribs={"layer": p.layer})
        elif isinstance(p, Polyline):
            pts = [(x, fy(y)) for x, y in p.points]
            msp.add_lwpolyline(pts, close=p.closed, dxfattribs=_attribs(p, p.layer))
        elif isinstance(p, Text):
            attribs = _attribs(p, p.layer)  # carry Text.color → true_color (status/disclaimer)
            attribs.update({"height": p.height, "rotation": p.rotation})
            t = msp.add_text(p.text, dxfattribs=attribs)
            t.set_placement((p.x, fy(p.y)), align=_ALIGN[p.anchor])
        else:  # pragma: no cover - defensive
            raise TypeError(f"unknown primitive: {type(p).__name__}")

    return _write_deterministic(doc)
