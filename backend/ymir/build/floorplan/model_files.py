"""Building-model files from the Stoa Lens PNG-to-IFC converter, run as it was written.

Build's wall lines are a re-vectorised view of the converter's wall mask and drop most of what the converter
finds on thin-line or dense sheets. This module runs the converter's own entry point
(`stoa_bim.png_to_ifc.run`: walls as polygons, doors, windows, stairs, rooms, then the IFC / GLB / DXF / SVG /
PDF writers) on the full-resolution page, unchanged, and keeps its files beside the drawing.

Needs the research requirements (ifcopenshell, ezdxf, pymupdf, scipy, shapely). When they are missing the
drawing is still built; the reason is recorded.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .converter import RESEARCH_SRC

import threading

_LOCK = threading.Lock()
TUNED_MPP = 0.017
FILES = {"model.ifc": "application/x-step", "model.glb": "model/gltf-binary", "model.dxf": "image/vnd.dxf",
         "model.svg": "image/svg+xml", "model.pdf": "application/pdf", "model.json": "application/json"}


def available() -> tuple[bool, str | None]:
    if not (RESEARCH_SRC / "stoa_bim" / "png_to_ifc.py").is_file():
        return False, "research/stoa-lens converter not present"
    for name in ("ifcopenshell", "ezdxf", "pymupdf", "scipy", "shapely"):
        try:
            __import__(name)
        except ImportError:
            return False, f"converter dependency not installed: {name}"
    return True, None


def convert(image_path, out_dir, m_per_px: float, crop=None, scale_source: str = "estimate") -> dict:
    """Run the converter on one page image. Returns a record for the drawing payload; never raises."""
    ok, why = available()
    record = {"method": "stoa_bim.png_to_ifc", "m_per_px": round(float(m_per_px), 6), "scale_source": scale_source,
              "crop": [int(v) for v in crop] if crop else None}
    if not ok:
        return {**record, "status": "unavailable", "reason": why}
    if str(RESEARCH_SRC) not in sys.path:
        sys.path.insert(0, str(RESEARCH_SRC))
    t = time.perf_counter()
    try:
        from stoa_bim import png_to_ifc
        # The converter names a page after its file and keeps per-page work under that name. Every prepared
        # upload is called page-N.png, so give it a name unique to this drawing.
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        page = out / f"{out.name.removesuffix('.model')}-page.png"
        # The converter's sizes are tuned for about 0.017 m per pixel (a 1:100 sheet at 150 DPI). A much finer
        # scan is reduced to that first; coordinates are mapped back in payload_geometry.
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        factor = 1.0
        with Image.open(image_path) as im:
            if m_per_px < 0.7 * TUNED_MPP:
                factor = m_per_px / TUNED_MPP
                im = im.convert("RGB").resize((max(1, round(im.width * factor)), max(1, round(im.height * factor))), Image.LANCZOS)
            im.convert("RGB").save(page)
        if factor != 1.0:
            m_per_px = m_per_px / factor
            if record["crop"]:
                record["crop"] = [int(round(v * factor)) for v in record["crop"]]
        record.update(converter_image_factor=round(factor, 4), converter_m_per_px=round(float(m_per_px), 6))
        # For line-drawn plans the converter swaps its wall finder for one that returns this page's walls, and
        # leaves it swapped. In a server the next drawing would get this page's walls, so run one conversion
        # at a time and put the finder back.
        from stoa_bim import generate
        with _LOCK:
            finder, detections, local_detect = generate.wall_mask, generate.DETECTIONS, png_to_ifc.detect
            try:
                split = _remote_openings(page, out, float(m_per_px), generate, record)
                if split:  # the boxes are already in place; skip the converter's local model run
                    png_to_ifc.detect = lambda *_a, _s=split: _s
                style = wall_style(png_to_ifc, generate, page, record["crop"], float(m_per_px))
                record["wall_style"] = style
                # The converter's own text masking: OCR words are kept out of the walls (and name the rooms).
                from .. import ocr as _ocr
                mask_text = _ocr.available()
                record["text_masked"] = mask_text
                counts = png_to_ifc.run(str(page), str(out_dir), float(m_per_px), record["crop"], mask_text,
                                        walls=style["finder"], detector=bool(split))
            finally:
                generate.wall_mask, generate.DETECTIONS, png_to_ifc.detect = finder, detections, local_detect
    except Exception as exc:  # the drawing itself must still import
        return {**record, "status": "failed", "reason": type(exc).__name__ + ": " + str(exc)[:200]}
    out = Path(out_dir)
    return {**record, "status": "built", "counts": counts, "seconds": round(time.perf_counter() - t, 1),
            "files": [n for n in FILES if (out / n).is_file()],
            "note": "Heights are defaults. Sizes are only as good as the scale: " + scale_source + "."}


def _without_holes(poly, depth=0):
    """Split a polygon that has holes into hole-free pieces, cutting through each hole. The viewer extrudes
    plain outlines; a wall ring around a room must not be extruded as a solid slab."""
    from shapely.geometry import LineString
    from shapely.ops import split
    if poly.is_empty or poly.geom_type != "Polygon":
        return [g for p in getattr(poly, "geoms", []) for g in _without_holes(p, depth)]
    if not poly.interiors or depth > 40:
        return [poly]
    from shapely.geometry import Polygon
    x = Polygon(poly.interiors[0]).representative_point().x
    _, y0, _, y1 = poly.bounds
    out = []
    for piece in split(poly, LineString([(x, y0 - 1), (x, y1 + 1)])).geoms:
        out += _without_holes(piece, depth + 1)
    return out


def payload_geometry(out_dir, min_block_px2: float = 4.0, image_factor: float = 1.0) -> dict:
    """The converter's model.json as the drawing payload's geometry, in page pixels:
    walls as outlines to extrude (`wall_blocks`), rooms from its spaces, and its openings as boxes."""
    import json

    from shapely.geometry import Polygon
    m = json.loads((Path(out_dir) / "model.json").read_text())
    k = m["frame"]["m_per_px"] * image_factor  # metres per pixel of the original image

    def px(pts):
        return [[round(x / k, 1), round(-y / k, 1)] for x, y in pts]

    blocks = []
    for w in m["walls"]:
        shape = Polygon(px(w["polygon"]), [px(h) for h in w.get("holes") or []]).buffer(0)
        for piece in _without_holes(shape):
            if piece.area >= min_block_px2:
                blocks.append({"id": f"k{len(blocks) + 1}", "polygon": [[round(x, 1), round(y, 1)] for x, y in piece.exterior.coords[:-1]],
                               "source": w["id"]})
    rooms = [{"id": f"r{i + 1}", "polygon": px(s["polygon"]), "polygon_basis": "inner_faces",
              **({"label": s["name"]} if s.get("name") else {})} for i, s in enumerate(m.get("spaces", []))]
    openings = []
    for kind, key in (("door", "doors"), ("window", "windows")):
        for e in m.get(key, []):
            pts = px(e["polygon"])
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            openings.append({"id": f"o{len(openings) + 1}", "kind": kind, "box": [min(xs), min(ys), max(xs), max(ys)]})
    return {"wall_blocks": blocks, "rooms": rooms, "openings": openings}


def _remote_openings(page: Path, out: Path, m_per_px: float, generate, record: dict) -> bool:
    """With YMIR_OPENINGS_BACKEND=modal, get door / window / fixture boxes from the Modal detector
    (deploy/modal_openings.py) and leave them where the converter's `--detector` option reads them. The image is
    resized to the detector's training scale first, as the converter does locally. Any failure is recorded and
    the conversion goes on without the detector."""
    import io
    import json
    import os
    if os.environ.get("YMIR_OPENINGS_BACKEND") != "modal":
        return False
    try:
        import modal
        from PIL import Image
        from stoa_bim.detect_openings import TRAIN_MPP
        factor = min(1.0, m_per_px / TRAIN_MPP)
        with Image.open(page) as im:
            small = im.convert("RGB").resize((max(1, int(im.width * factor)), max(1, int(im.height * factor))), Image.LANCZOS)
        buf = io.BytesIO()
        small.save(buf, format="PNG")
        reply = modal.Cls.from_name("ymir-openings", "Openings")().detect.remote(buf.getvalue())
        generate.DETECTIONS = out / "detections"
        generate.DETECTIONS.mkdir(parents=True, exist_ok=True)
        rows = [[c, s, x0 / factor, y0 / factor, x1 / factor, y1 / factor] for c, s, x0, y0, x1, y1 in reply["detections"]]
        (generate.DETECTIONS / f"user-{out.name}.json").write_text(json.dumps(
            {"weights": "modal:ymir-openings " + reply["weights_sha256"], "train_mpp": TRAIN_MPP, "pages": {page.stem: rows}}))
        record["detector"] = {"backend": "modal", "weights_sha256": reply["weights_sha256"], "detections": len(rows)}
        return True
    except Exception as exc:
        record["detector"] = {"backend": "modal", "status": "failed", "reason": type(exc).__name__ + ": " + str(exc)[:160]}
        return False


TRAIN_MPP = 0.0339  # the detector's training scale (stoa_bim.detect_openings.TRAIN_MPP)


def _remote_openings(page: Path, out: Path, m_per_px: float, generate, record: dict):
    """With YMIR_OPENINGS_BACKEND=modal, get door / window / fixture boxes from the Modal detector
    (deploy/modal_openings.py) and put them where the converter's `--detector` option reads them. The image is
    resized to the detector's training scale first, as the converter does locally. Returns the detection set's
    name, or None. Any failure is recorded and the conversion goes on without the detector."""
    import io
    import json
    import os
    if os.environ.get("YMIR_OPENINGS_BACKEND") != "modal":
        return None
    try:
        import modal
        from PIL import Image
        factor = min(1.0, m_per_px / TRAIN_MPP)
        with Image.open(page) as im:
            small = im.convert("RGB").resize((max(1, int(im.width * factor)), max(1, int(im.height * factor))), Image.LANCZOS)
        buf = io.BytesIO()
        small.save(buf, format="PNG")
        reply = modal.Cls.from_name("ymir-openings", "Openings")().detect.remote(buf.getvalue())
        generate.DETECTIONS = out / "detections"
        generate.DETECTIONS.mkdir(parents=True, exist_ok=True)
        split = f"user-{out.name}"
        rows = [[c, s, x0 / factor, y0 / factor, x1 / factor, y1 / factor] for c, s, x0, y0, x1, y1 in reply["detections"]]
        (generate.DETECTIONS / f"{split}.json").write_text(json.dumps(
            {"weights": "modal:ymir-openings " + reply["weights_sha256"], "train_mpp": TRAIN_MPP, "pages": {page.stem: rows}}))
        record["detector"] = {"backend": "modal", "weights_sha256": reply["weights_sha256"], "detections": len(rows)}
        return split
    except Exception as exc:
        record["detector"] = {"backend": "modal", "status": "failed", "reason": type(exc).__name__ + ": " + str(exc)[:160]}
        return None


def wall_style(png_to_ifc, generate, page: Path, crop, m_per_px: float) -> dict:
    """Which of the converter's wall finders suits this drawing: 'thick' (solid or hatched walls) or 'lines'
    (thin or double-line walls, colour-filled rooms).

    The converter's own choice compares the two over the whole plan area. The app passes nearly the whole sheet,
    so frames, legends and balcony outlines make every sheet look line-drawn. Here the comparison is made only
    inside the building: the area the thick walls enclose."""
    import numpy as np
    from PIL import Image
    with Image.open(page) as im:
        W, H = im.size
    info = {"id": page.stem, "width": W, "height": H, "crop": list(crop) if crop else [0, 0, W, H],
            "m_per_px": m_per_px, "image_abs": str(page)}
    gray, sat = generate._gray(info)
    thick, _ = generate.wall_mask(info, gray, sat, [])
    lines, _ = png_to_ifc.line_walls(info, gray, [])
    x0, y0, x1, y1 = info["crop"]
    T, Lm, S = thick[y0:y1, x0:x1], lines[y0:y1, x0:x1], sat[y0:y1, x0:x1]
    out = {"thick_share": round(float(T.mean()), 4), "coloured_share": round(float((T & (S > 40)).sum() / max(1, T.sum())), 3)}
    if T.mean() < 0.003:
        return {**out, "finder": "lines", "why": "almost no thick dark wall"}
    if out["coloured_share"] > 0.3:
        return {**out, "finder": "lines", "why": "the thick dark areas are mostly coloured room fill"}
    ys, xs = np.nonzero(T)
    bx0, bx1 = np.percentile(xs, [1, 99]).astype(int)
    by0, by1 = np.percentile(ys, [1, 99]).astype(int)
    inside_t, inside_l = T[by0:by1 + 1, bx0:bx1 + 1].mean(), Lm[by0:by1 + 1, bx0:bx1 + 1].mean()
    out["lines_to_thick_in_building"] = round(float(inside_l / max(inside_t, 1e-9)), 2)
    if inside_l > 3 * inside_t:
        return {**out, "finder": "lines", "why": "inside the building, line walls cover over three times the thick walls"}
    return {**out, "finder": "thick", "why": "solid or hatched walls found"}
