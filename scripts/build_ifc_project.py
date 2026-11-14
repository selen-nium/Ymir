"""Build the sample multi-sheet project (data/samples) from one IFC model in the Junction dataset.

usage (repo root; needs `pip install ifcopenshell numpy pillow shapely`, not part of the app's requirements):
    python scripts/build_ifc_project.py /path/to/junction-dataset-preview/data/ifc_bench/ac20_arc.ifc [out_dir]

Renders plan sheets per storey (a cut at 1.1 m: walls filled, things below in outline, rooms labelled), a roof
plan and a south elevation, all in one frame so they arrive placed. One storey is rendered with its own crop
and left unplaced, so the project also shows the Place step. Writes <id>.png/.json/.trace.json per sheet and
the project into projects.json.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ue
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import polygonize, unary_union

ROOT = Path(__file__).resolve().parents[1]
K = 24                      # px per metre (1:100 at ~60 dpi)
CUT = 1.1                   # plan cut height above the storey, m
PROJECT = {"id": "ac20", "title": "AC20 institute building",
           "source": {"dataset": "Stoa · ifc_bench", "file": "ifc_bench/ac20_arc.ifc",
                      "licence": "KIT, unrestricted use",
                      "credit": "IAI / Karlsruhe Institute of Technology (KIT), KIT IFC Examples"}}
# storey name → (sheet id, sheet number, title, kind, level id, placed)
SHEETS = {
    "Keller": ("ac20-kg", "A-099", "Basement plan", "floor_plan", "KG", True),
    "Erdgeschoss": ("ac20-eg", "A-100", "Ground floor plan", "floor_plan", "EG", True),
    "1. Obergeschoss": ("ac20-og1", "A-101", "Level 1 plan", "floor_plan", "OG1", False),
    "Dachgeschoss": ("ac20-dg", "A-104", "Roof plan", "roof_plan", "DG", True),
}
LEVELS = [("F", "Foundation", -3.6, 0.6), ("KG", "Basement", -3.0, 3.0), ("EG", "Ground floor", 0.0, 3.0),
          ("OG1", "Level 1", 3.0, 3.0), ("OG2", "Level 2", 6.0, 3.0), ("DG", "Roof", 9.0, 2.5)]
CUT_TYPES = ("IfcWall", "IfcWallStandardCase", "IfcColumn")
OUTLINE_TYPES = ("IfcDoor", "IfcWindow", "IfcStair", "IfcRailing", "IfcFurnishingElement", "IfcSlab")
def font(size):
    """A system font with '²' in it; Pillow's built-in one draws a box instead."""
    for name in ("Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


FONT, SMALL = font(12), font(10)


def shapes(f, elements):
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    out = {}
    for el in elements:
        try:
            g = ifcopenshell.geom.create_shape(s, el).geometry
        except Exception:
            continue
        v = np.array(g.verts, dtype=float).reshape(-1, 3)
        t = np.array(g.faces, dtype=int).reshape(-1, 3)
        if len(t):
            out[el.id()] = (v, t)
    return out


def section(v, t, z):
    """Closed outlines where the plane at height z cuts a triangle mesh."""
    segs = []
    for tri in t:
        zs = v[tri, 2]
        if zs.min() >= z or zs.max() <= z:
            continue
        pts = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = sorted((tri[i], tri[j]))
            za, zb = v[a, 2], v[b, 2]
            if (za - z) * (zb - z) < 0:
                r = (z - za) / (zb - za)
                p = v[a] + r * (v[b] - v[a])
                pts.append((round(p[0], 6), round(p[1], 6)))
        if len(pts) == 2 and pts[0] != pts[1]:
            segs.append(LineString(pts))
    if not segs:
        return None
    result = Polygon()
    for p in sorted(polygonize(unary_union(segs)), key=lambda p: -p.area):
        result = result.symmetric_difference(p)
    return result if not result.is_empty else None


def footprint(v, t, zmin=-1e9, zmax=1e9):
    tris = [Polygon(v[tri, :2]) for tri in t if v[tri, 2].max() > zmin and v[tri, 2].min() < zmax]
    tris = [p for p in tris if p.is_valid and p.area > 1e-8]
    return unary_union(tris) if tris else None


def polys(g):
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


class Sheet:
    """A white canvas over a world frame [x0, y0, x1, y1] (m); image y points south."""

    def __init__(self, frame, height_m=None, z_top=None):
        self.x0, self.y0, self.x1, self.y1 = frame
        self.z_top = z_top
        w = round((self.x1 - self.x0) * K)
        h = round(((self.y1 - self.y0) if height_m is None else height_m) * K)
        self.img = Image.new("RGB", (w, h), "white")
        self.draw = ImageDraw.Draw(self.img)
        self.texts = []

    def px(self, x, y):
        return ((x - self.x0) * K, (self.y1 - y) * K)

    def ez(self, x, z):              # elevation: x across, z up
        return ((x - self.x0) * K, (self.z_top - z) * K)

    def poly(self, p, fill=None, outline=None, width=1, to=None):
        to = to or self.px
        for q in polys(p):
            self.draw.polygon([to(*c) for c in q.exterior.coords], fill=fill, outline=outline, width=width)
            for hole in q.interiors:
                self.draw.polygon([to(*c) for c in hole.coords], fill="white", outline=outline, width=width)

    def text(self, xy, s, font=FONT, anchor="mm", fill="#222"):
        self.draw.text(xy, s, font=font, anchor=anchor, fill=fill)
        b = self.draw.textbbox(xy, s, font=font, anchor=anchor)
        self.texts.append({"id": f"t{len(self.texts) + 1}", "text": s, "box": [round(c, 1) for c in b], "confidence": 1.0})
        return b

    def title(self, number, name, top=False):
        w, h = self.img.size
        y = 8 if top else h - 46
        self.draw.rectangle([w - 250, y, w - 8, y + 38], outline="#222")
        self.text((w - 240, y + 13), f"{number}  {name}", anchor="lm")
        self.text((w - 240, y + 29), "AC20 institute · from ac20_arc.ifc · 1:100", font=SMALL, anchor="lm", fill="#555")


def plan_sheet(frame, storey, z, els, geo, number, name):
    sh = Sheet(frame)
    zc = z + CUT
    walls, rooms = [], []
    for el in els:
        g = geo.get(el.id())
        if not g or el.is_a("IfcSpace"):
            continue
        v, t = g
        if el.is_a() in CUT_TYPES:
            continue
        if el.is_a("IfcSlab") and storey != "Dachgeschoss":
            continue
        fp = footprint(v, t, zmax=zc) if not el.is_a("IfcSlab") else footprint(v, t)
        if fp is not None:
            sh.poly(fp, outline="#9a9a9a" if el.is_a("IfcFurnishingElement") else "#555")
    for el in els:
        g = geo.get(el.id())
        if not g or el.is_a() not in CUT_TYPES:
            continue
        v, t = g
        cut = section(v, t, zc)
        if cut is not None:
            sh.poly(cut, fill="#1d1c1a", outline="#1d1c1a")
        full = footprint(v, t)
        if full is None or not el.is_a("IfcWall"):
            continue
        r = full.minimum_rotated_rectangle
        c = list(r.exterior.coords)[:4]
        e = [math.dist(c[i], c[(i + 1) % 4]) for i in range(4)]
        if max(e) < 2 * min(e) or max(e) < 0.4:
            continue
        i = 0 if e[0] >= e[1] else 1
        a = ((c[i][0] + c[(i + 3) % 4][0]) / 2, (c[i][1] + c[(i + 3) % 4][1]) / 2)
        b = ((c[i + 1][0] + c[i + 2][0]) / 2, (c[i + 1][1] + c[i + 2][1]) / 2)
        walls.append({"id": f"w{len(walls) + 1}", "a": [round(q, 1) for q in sh.px(*a)], "b": [round(q, 1) for q in sh.px(*b)],
                      "thickness_px": round(min(e) * K, 1), "confidence": 1.0, "ifc": el.GlobalId})
    for el in els:
        g = geo.get(el.id())
        if not g or not el.is_a("IfcSpace"):
            continue
        v, t = g
        p = section(v, t, z + 0.3)
        p = max(polys(p), key=lambda q: q.area, default=None)
        if p is None or p.area < 1:
            continue
        label = el.LongName or el.Name or "Room"
        cx, cy = sh.px(*p.representative_point().coords[0])
        b1 = sh.text((cx, cy - 8), label)
        sh.text((cx, cy + 8), f"{p.area:.2f} m²", font=SMALL, fill="#555")
        rooms.append({"id": f"space-{len(rooms) + 1}", "polygon": [[round(q, 1) for q in sh.px(*c)] for c in p.exterior.coords[:-1]],
                      "polygon_basis": "model", "type": "room", "label": label, "label_box": [round(q, 1) for q in b1],
                      "printed_area_m2": round(p.area, 2), "number": el.Name, "ifc": el.GlobalId})
    sh.title(number, name)
    return sh, walls, rooms


def elevation_sheet(frame, z_bottom, z_top, geo, f):
    """South elevation: everything above ground seen from the south, far things first."""
    sh = Sheet(frame, height_m=z_top - z_bottom, z_top=z_top)
    items = []
    for el in f.by_type("IfcProduct"):
        if el.is_a() in ("IfcSpace", "IfcOpeningElement", "IfcAnnotation", "IfcFurnishingElement", "IfcSite") or el.id() not in geo:
            continue
        v, t = geo[el.id()]
        items.append((v[:, 1].mean(), el, v, t))
    clip = box(frame[0], -0.2, frame[2], z_top)
    for _, el, v, t in sorted(items, key=lambda it: -it[0]):
        tris = [Polygon(v[tri][:, [0, 2]]) for tri in t]
        p = unary_union([q for q in tris if q.is_valid and q.area > 1e-8])
        if p.is_empty:
            continue
        fill = "#dfe6ea" if el.is_a("IfcWindow") else "#eeeeee" if el.is_a("IfcDoor") else "#d6d3cc" if el.is_a("IfcSlab") else "#f7f6f2"
        sh.poly(p.intersection(clip), fill=fill, outline="#333", to=sh.ez)
    gx = sh.ez(frame[0], 0)[1]
    sh.draw.line([(0, gx), (sh.img.size[0], gx)], fill="#1d1c1a", width=3)
    sh.title("A-201", "South elevation", top=True)
    return sh


def payload(sheet_id, title, sh, number, kind, walls=(), rooms=(), storey=None, z=None):
    w, h = sh.img.size
    return {"id": sheet_id, "title": title, "kind": kind, "sheet_number": number,
            "source": {**PROJECT["source"], "kind": "Rendered from IFC", "storey": storey},
            "image": {"url": f"data/{sheet_id}.png", "size": [w, h]},
            "scale": {"m_per_px": round(1 / K, 6), "source": "model", "confidence": 1.0, "text": "1:100"},
            "default_height_m": 3.0, "plans": [{"id": "p1", "box": [0, 0, w, h]}],
            "walls": list(walls), "openings": [], "rooms": list(rooms), "symbols": [], "texts": sh.texts}


def trace(summary, ms):
    return [{"type": "node", "id": "ifc", "name": "Read IFC model", "tool": "IfcOpenShell", "parents": []},
            {"type": "status", "id": "ifc", "state": "done", "summary": summary, "ms": ms}]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    t0 = time.time()
    f = ifcopenshell.open(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / "samples"
    products = [e for e in f.by_type("IfcProduct") if not e.is_a("IfcOpeningElement") and not e.is_a("IfcSite")
                and not e.is_a("IfcAnnotation") and not e.is_a("IfcSpatialStructureElement")]
    geo = shapes(f, products + list(f.by_type("IfcSpace")))
    ms = round((time.time() - t0) * 1000)
    allv = np.vstack([v for v, _ in geo.values()])
    pad = 2.0
    bx0, by0, bx1, by1 = allv[:, 0].min(), allv[:, 1].min(), allv[:, 0].max(), allv[:, 1].max()
    frame = (bx0 - pad, by0 - pad, bx1 + pad, by1 + pad)
    storeys = {s.Name: s for s in f.by_type("IfcBuildingStorey")}
    sheets, written = [], []
    for name, (sid, number, title, kind, level, placed) in SHEETS.items():
        s = storeys[name]
        els = ue.get_decomposition(s)
        own = frame
        if not placed:                  # own crop, so it has to be placed by hand
            vv = np.vstack([geo[e.id()][0] for e in els if e.id() in geo])
            own = (vv[:, 0].min() - 3.5, vv[:, 1].min() - 1.0, vv[:, 0].max() + 1.0, vv[:, 1].max() + 3.0)
        sh, walls, rooms = plan_sheet(own, name, float(s.Elevation), els, geo, number, title)
        sh.img.save(out / f"{sid}.png", optimize=True)
        p = payload(sid, title, sh, number, kind, walls, rooms, storey=name, z=float(s.Elevation))
        (out / f"{sid}.json").write_text(json.dumps(p, ensure_ascii=False))
        (out / f"{sid}.trace.json").write_text(json.dumps(trace(f"{len(walls)} walls · {len(rooms)} rooms", ms), indent=1))
        tf = [round(1 / K, 6), 0, round(own[0] - frame[0], 4), round(frame[3] - own[3], 4)]
        sheets.append({"drawing_id": sid, "views": [{"box": None, "kind": kind, "discipline": "architecture",
                       "placement": {"type": "plan", "level": level, "transform": tf if placed else None,
                                     "by": "model" if placed else None}}]})
        written.append(sid)
    z_top = float(allv[:, 2].max()) + 1.0
    el = elevation_sheet(frame, -0.6, z_top, geo, f)
    el.img.save(out / "ac20-south.png", optimize=True)
    (out / "ac20-south.json").write_text(json.dumps(payload("ac20-south", "South elevation", el, "A-201", "elevation"), ensure_ascii=False))
    (out / "ac20-south.trace.json").write_text(json.dumps(trace("Seen from the south", ms), indent=1))
    u0, u1 = (bx0 - frame[0]) * K, (bx1 - frame[0]) * K
    v0 = (z_top - 0) * K
    ys = frame[3] - by0
    sheets.append({"drawing_id": "ac20-south", "views": [{"box": None, "kind": "elevation", "discipline": "architecture",
                   "placement": {"type": "elevation", "level": "EG", "by": "model",
                                 "base": [[round(bx0 - frame[0], 4), round(ys, 4)], [round(bx1 - frame[0], 4), round(ys, 4)]],
                                 "sheet_ground": [[round(u0, 1), round(v0, 1)], [round(u1, 1), round(v0, 1)]]}}]})
    order = ["ac20-eg", "ac20-kg", "ac20-dg", "ac20-south", "ac20-og1"]
    sheets.sort(key=lambda s: order.index(s["drawing_id"]))
    project = {**PROJECT, "thumbnail": "api/drawings/ac20-eg/image",
               "levels": [{"id": i, "name": n, "z_m": z, "height_m": hm} for i, n, z, hm in LEVELS], "sheets": sheets}
    manifest = out / "projects.json"
    projects = [p for p in (json.loads(manifest.read_text()) if manifest.is_file() else []) if p["id"] != PROJECT["id"]]
    manifest.write_text(json.dumps([project, *projects], ensure_ascii=False, indent=1))
    print(f"wrote {', '.join(written)}, ac20-south and projects.json in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
