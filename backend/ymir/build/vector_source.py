"""A 3D model from an uploaded vector file (IFC, SVG, DXF) when no rooms were picked by hand.

- IFC: the building is already in the file. Its plan section (walls, doors, windows cut at 1.2 m above the
  lowest storey that has walls) becomes the drawing's walls and openings directly, in true metres, and the
  IFC itself plus a GLB are kept as the model files. Nothing is detected.
- DXF and SVG: the file is drawn to an image and the PNG converter builds from that. A DXF with length units
  keeps its true scale; an SVG has none, so the scale is read from a printed scale bar or estimated.
"""
from __future__ import annotations

import sys
from pathlib import Path

LONG_SIDE = 4000  # pixels on the longer side of the rendered page
DXF_UNITS = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}  # $INSUNITS -> metres


def render_svg(path, png) -> dict:
    import pymupdf
    with pymupdf.open(str(path)) as doc:
        page = doc[0]
        zoom = LONG_SIDE / max(page.rect.width, page.rect.height)
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, colorspace=pymupdf.csRGB).save(str(png))
    return {"rendered_from": "svg", "m_per_px": None}


def render_dxf(path, png) -> dict:
    import ezdxf
    from ezdxf import bbox
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    ext = bbox.extents(msp)
    if not ext.has_data:
        raise ValueError("The DXF has no drawable geometry.")
    w, h = ext.size.x, ext.size.y
    if min(w, h) <= 0:
        raise ValueError("The DXF geometry has no area.")
    backend = PyMuPdfBackend()
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE, color_policy=config.ColorPolicy.BLACK)
    Frontend(RenderContext(doc), backend, config=cfg).draw_layout(msp)
    mm = 400.0 / max(w, h)  # page is the drawing's extents, 400 mm on the long side, no margins
    page = layout.Page(w * mm, h * mm, layout.Units.mm, margins=layout.Margins.all(0))
    dpi = LONG_SIDE / (400.0 / 25.4)
    Path(png).write_bytes(backend.get_pixmap_bytes(page, fmt="png", dpi=round(dpi)))
    from PIL import Image
    with Image.open(png) as im:
        px = im.width
    unit = DXF_UNITS.get(doc.units)
    return {"rendered_from": "dxf", "m_per_px": (w * unit / px) if unit else None,
            "scale_source": "DXF length units" if unit else None}


def render_ifc(path, png, research_src) -> dict:
    """Plan section of the lowest storey with walls, drawn as a plan image; geometry returned in image pixels."""
    import cv2
    import ifcopenshell
    import numpy as np
    if str(research_src) not in sys.path:
        sys.path.insert(0, str(research_src))
    from stoa_bim import section as S
    model = ifcopenshell.open(str(path))
    levels = sorted({float(s.Elevation or 0.0) for s in model.by_type("IfcBuildingStorey")}) or [0.0]
    scale = 1.0
    try:
        import ifcopenshell.util.unit as U
        scale = U.calculate_unit_scale(model)  # storey elevations are in file units; geometry comes out in metres
    except Exception:
        pass
    sec, level = {}, levels[0] * scale
    for z in levels:
        sec = S.section(path, z=z * scale + S.CUT)
        if sec.get("IfcWall"):
            level = z * scale
            break
    walls = [p for g in sec.get("IfcWall", []) for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and p.area > 0]
    if not walls:
        raise ValueError("No wall could be cut from this IFC.")
    xs = [c for p in walls for c in (p.bounds[0], p.bounds[2])]
    ys = [c for p in walls for c in (p.bounds[1], p.bounds[3])]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    k = max(x1 - x0, y1 - y0) / (LONG_SIDE * 0.9)  # metres per pixel
    pad = LONG_SIDE * 0.05
    W, H = int((x1 - x0) / k + 2 * pad), int((y1 - y0) / k + 2 * pad)

    def px(coords):
        return [[round((x - x0) / k + pad, 1), round((y1 - y) / k + pad, 1)] for x, y, *_ in coords]

    img = np.full((H, W, 3), 255, np.uint8)
    blocks, openings = [], []
    for p in walls:
        ring = px(p.exterior.coords[:-1])
        cv2.fillPoly(img, [np.round(np.array(ring)).astype(np.int32)], (60, 60, 60))
        for hole in p.interiors:
            cv2.fillPoly(img, [np.round(np.array(px(hole.coords[:-1]))).astype(np.int32)], (255, 255, 255))
        blocks.append((p, ring))
    for cls, kind in (("IfcDoor", "door"), ("IfcWindow", "window")):
        for g in sec.get(cls, []):
            if g.is_empty:
                continue
            bx0, by0, bx1, by1 = g.bounds
            a, b = px([(bx0, by1), (bx1, by0)])
            cv2.rectangle(img, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), (0, 0, 0), 2)
            openings.append({"id": f"o{len(openings) + 1}", "kind": kind, "box": [a[0], a[1], b[0], b[1]]})
    cv2.imwrite(str(png), img)
    # Wall outlines without holes, as the viewer extrudes plain outlines.
    from shapely.geometry import Polygon

    from .floorplan.model_files import _without_holes
    wall_blocks = []
    for p, _ in blocks:
        shape = Polygon(px(p.exterior.coords[:-1]), [px(hh.coords[:-1]) for hh in p.interiors]).buffer(0)
        for piece in _without_holes(shape):
            if piece.area >= 4:
                wall_blocks.append({"id": f"k{len(wall_blocks) + 1}", "polygon": [[round(x, 1), round(y, 1)] for x, y in piece.exterior.coords[:-1]]})
    return {"rendered_from": "ifc", "m_per_px": k, "scale_source": "IFC geometry (metres)", "storey_level_m": round(level, 3),
            "geometry": {"wall_blocks": wall_blocks, "openings": openings},
            "counts": {"walls": len(walls), "doors": sum(o["kind"] == "door" for o in openings),
                       "windows": sum(o["kind"] == "window" for o in openings)}}


def render(path, fmt, png, research_src) -> dict:
    if fmt == "svg":
        return render_svg(path, png)
    if fmt == "dxf":
        return render_dxf(path, png)
    if fmt == "ifc":
        return render_ifc(path, png, research_src)
    raise ValueError("Unsupported vector format: " + fmt)
