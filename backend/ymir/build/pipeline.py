"""Drawing → payload: read, crop, detect walls (WallNet or OpenCV lines), read text, set scale, find rooms.

Returns the drawing payload (frontend contract), the build trace (node/status events) and the cropped image.
Used by uploads (POST /api/drawings) and scripts/build_samples.py.
"""
from __future__ import annotations

import io
import math
import re
import time
from dataclasses import dataclass

from . import ocr

LETTERS_ROOM = re.compile(r"[A-Za-zÄÖÜäöü/ ]{3,}")
ROOM_NUMBER = re.compile(r"\d\.\d{2}")
ROOM_TYPES = [(r"k[uü]che|kuche|kitchen", "kitchen"), (r"zimme|bedroom", "room"), (r"ko[rm]+idor|corridor", "corridor"),
              (r"reduit|storage", "storage"), (r"wohnen|living", "living"), (r"treppen|stair", "stair"),
              (r"\bbad\b|bath", "bathroom"), (r"balkon|balcony", "balcony")]


@dataclass
class Built:
    payload: dict
    trace: list
    image: bytes      # cropped plan image
    ext: str          # "png" or "jpg"


def _room_type(name):
    return next((ty for pat, ty in ROOM_TYPES if re.search(pat, name, re.I)), None)


def _union_box(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _trace(steps):
    ev = []
    for sid, name, tool, parents, state, summary, ms in steps:
        ev.append({"type": "node", "id": sid, "name": name, "tool": tool, "parents": parents})
        st = {"type": "status", "id": sid, "state": state, "summary": summary}
        if ms is not None and state == "done":
            st["ms"] = int(round(ms))
        ev.append(st)
    return ev


def auto_crop(png):
    """Bounding box of wall proposals after dropping near-full-width/height lines (page frames)."""
    import cv2
    import numpy as np
    from .floorplan.extract import propose
    g = cv2.imdecode(np.frombuffer(png, np.uint8), 0)
    H, W = g.shape
    walls, _ = propose(png, (0, 0, W, H))
    keep = [w for w in walls if abs(w["b"][0] - w["a"][0]) < .85 * W and abs(w["b"][1] - w["a"][1]) < .85 * H]
    if not keep:
        return (0, 0, W, H)
    xs = [p[0] for w in keep for p in (w["a"], w["b"])]
    ys = [p[1] for w in keep for p in (w["a"], w["b"])]
    pad = 20
    return (max(0, min(xs) - pad), max(0, min(ys) - pad), min(W, max(xs) + pad), min(H, max(ys) + pad))


def build_drawing(raw: bytes, *, drawing_id: str, title: str, dataset: str, kind: str, file: str,
                  crop=None, scale_denominator: int | None = None, scale_text: str | None = None,
                  read_text: bool = False, detector: str = "auto") -> Built:
    """scale_denominator: 1:N sheet scale on an A3 page (else the scale is estimated from image size).
    read_text: legacy offline text extraction, disabled for live imports."""
    import cv2
    import numpy as np
    from PIL import Image
    from shapely.geometry import Point, Polygon

    from .floorplan.extract import LOW_CONFIDENCE, normalize, propose
    from .floorplan.model import Plan, validate
    from .floorplan.rooms import recover_rooms, split_rooms
    from .floorplan.vectorize import clean as vclean

    t_read = time.perf_counter()
    png, src = normalize(raw)
    W, H = src["width"], src["height"]
    crop = tuple(int(round(v)) for v in (crop or auto_crop(png)))
    t_read = (time.perf_counter() - t_read) * 1000
    walls, ext = propose(png, crop, detector=detector)
    learned = ext["method"].startswith("learned")
    merged = walls if learned else vclean(walls)
    merged = [w for w in merged if math.dist(w["a"], w["b"]) >= 12]  # drop tiny fragments
    x0, y0, x1, y1 = crop
    for i, w in enumerate(merged):
        w["id"] = f"w{i + 1}"
        w["status"] = "accepted"

    sheet = scale_denominator is not None
    m_per_px = scale_denominator / 1000 / (W / 420.0) if sheet else (0.02 if W > 1500 else 0.03 if W > 600 else 0.04)
    refs = [{"a": (x0 + 2, y0 + 2), "b": (x1 - 2, y0 + 2), "length_m": (x1 - x0 - 4) * m_per_px},
            {"a": (x0 + 2, y0 + 2), "b": (x0 + 2, y1 - 2), "length_m": (y1 - y0 - 4) * m_per_px}]
    plan = Plan(name=drawing_id, crop=crop, walls=merged, references=refs,
                dimensions_confirmed=True, geometry_confirmed=True, reviewer="pipeline")
    v = validate(plan, W, H)
    errors = [i for i in v["issues"] if i["severity"] == "error"]
    dangling = next((set(i["element_ids"]) for i in v["issues"] if i["code"] == "unjoined"), set())
    overlap = {e for i in errors if i["code"] == "overlap" for e in i["element_ids"]}

    out_walls = []  # crop-relative pixels for the frontend
    for w in merged:
        length = math.dist(w["a"], w["b"]) * m_per_px
        conf = w.get("confidence")
        flag = ("overlap" if w["id"] in overlap else "low_confidence" if conf is not None and conf < LOW_CONFIDENCE
                else "unjoined" if w["id"] in dangling and conf is None else ("short" if length < .6 else None))
        out_walls.append({"id": w["id"], "a": [w["a"][0] - x0, w["a"][1] - y0], "b": [w["b"][0] - x0, w["b"][1] - y0],
                          "length_m": round(length, 2), "flag": flag, "confidence": conf,
                          "thickness_px": round(w["thickness_px"], 1) if w.get("thickness_px") else None})
    # rooms back to crop pixels (model frame: x right, y up from the crop's bottom-left)
    rooms = [{"id": r["id"], "polygon": [[round(p[0] / m_per_px, 1), round((y1 - y0) - p[1] / m_per_px, 1)] for p in r["polygon_m"]],
              "area_m2": round(r["area_m2"], 2), "label": None, "printed_area_m2": None} for r in v["rooms"]]
    gaps_px = [{"a": [g["a"][0] / m_per_px, (y1 - y0) - g["a"][1] / m_per_px],
                "b": [g["b"][0] / m_per_px, (y1 - y0) - g["b"][1] / m_per_px]} for g in v.get("door_gaps", [])]

    crop_img = Image.open(io.BytesIO(png)).convert("RGB").crop(crop)
    labels = []
    t_ocr = t_rooms = 0.0
    if read_text:
        t = time.perf_counter()
        bgr = cv2.cvtColor(np.array(crop_img), cv2.COLOR_RGB2BGR)
        labels = [{"text": d["text"].strip(), "box": [round(c, 1) for c in d["box"]], "score": round(d["score"], 3)}
                  for d in ocr.ocr_image(bgr)]
        t_ocr = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        # Rooms that swallowed a neighbour (2+ room numbers inside) are split from the labels as seeds,
        # with the wall mask and door-gap links as barriers.
        seeds = [(l["text"], (l["box"][0] + l["box"][2]) / 2, (l["box"][1] + l["box"][3]) / 2)
                 for l in labels if ROOM_NUMBER.fullmatch(l["text"])]
        barrier = np.zeros((y1 - y0, x1 - x0), np.uint8)
        for w in out_walls:
            cv2.line(barrier, tuple(map(int, w["a"])), tuple(map(int, w["b"])), 1, max(2, int(w.get("thickness_px") or 3)))
        if learned:
            from .floorplan import wallnet
            barrier |= (wallnet.predict(bgr) > .35).astype(np.uint8)
        rooms = split_rooms(rooms, seeds, barrier, gaps_px)
        rooms = recover_rooms(rooms, seeds, barrier, gaps_px, max_area_px=60 / m_per_px ** 2)
        for r in rooms:
            if r.get("split") or r.get("recovered"):
                r["area_m2"] = round(Polygon(r["polygon"]).area * m_per_px ** 2, 2)
            poly = Polygon(r["polygon"])
            inside = [l for l in labels if poly.contains(Point((l["box"][0] + l["box"][2]) / 2, (l["box"][1] + l["box"][3]) / 2))]
            name_l = [l for l in inside if LETTERS_ROOM.fullmatch(l["text"]) and l["text"] not in ("Whg.",)]
            num_l = [l for l in inside if ROOM_NUMBER.fullmatch(l["text"])]
            if name_l or num_l:
                r["label_box"] = _union_box([l["box"] for l in (name_l[:1] + num_l[:1])])
                r["label"] = " ".join(filter(None, [name_l[0]["text"] if name_l else None, num_l[0]["text"] if num_l else None]))
            if name_l and (rt := _room_type(name_l[0]["text"])):
                r["type"] = rt
            areas = [float(m.group(1).replace(",", ".")) for l in inside if (m := re.match(r"(\d+[.,]\d+)\s*m", l["text"]))]
            if len(areas) == 1:
                r["printed_area_m2"] = areas[0]
        t_rooms = (time.perf_counter() - t) * 1000

    ext_name = "jpg" if kind.lower().startswith("scan") else "png"
    buf = io.BytesIO()
    crop_img.save(buf, format="JPEG" if ext_name == "jpg" else "PNG", **({"quality": 82} if ext_name == "jpg" else {}), optimize=True)
    scale = {"m_per_px": m_per_px, "source": "sheet_note" if sheet else "estimate", "confidence": 0.9 if sheet else 0.2}
    if sheet and scale_text:
        scale["text"] = scale_text
    out_rooms = []
    for r in rooms:
        o = {"id": r["id"], "polygon": r["polygon"], "polygon_basis": "inner_faces" if (r.get("split") or r.get("recovered")) else "centreline"}
        o.update({k: r[k] for k in ("type", "label", "label_box", "printed_area_m2") if r.get(k) is not None})
        out_rooms.append(o)
    payload = {
        "id": drawing_id, "title": title,
        "source": {"file": file, "sha256": src["original_sha256"], "dataset": dataset, "kind": kind, "crop": list(crop)},
        "image": {"url": f"{drawing_id}.{ext_name}", "size": [x1 - x0, y1 - y0]},
        "scale": scale, "default_height_m": 2.7,
        "plans": [{"id": "p1", "box": [0, 0, x1 - x0, y1 - y0]}],
        "walls": [{k: val for k, val in {"id": w["id"], "a": [round(c, 1) for c in w["a"]], "b": [round(c, 1) for c in w["b"]],
                                         "thickness_px": w.get("thickness_px"), "confidence": w.get("confidence")}.items()
                   if val is not None} for w in out_walls],
        "openings": [], "rooms": out_rooms, "symbols": [],
        "texts": [{"id": f"t{i + 1}", "text": l["text"], "box": l["box"], "confidence": l["score"]} for i, l in enumerate(labels)],
    }
    trace = _trace([
        ("read", "Read drawing", "Pillow", [], "done", f"{kind} · {x1 - x0}×{y1 - y0} px", t_read),
        ("plan", "Find plan", "OpenCV", ["read"], "done", "Plan region cropped", 30),
        ("walls", "Detect walls", "WallNet" if learned else "OpenCV lines", ["plan"], "done", f"{len(out_walls)} walls found", ext["seconds"] * 1000),
        *([("text", "Read text", "Text extraction", ["plan"], "done", f"{len(labels)} labels", t_ocr)] if labels else []),
        ("scale", "Read scale", "Source metadata" if sheet else "Estimate", ["plan"], "done",
         f"Read “{scale_text}”" if sheet and scale_text else "No scale printed: estimate", 20),
        ("rooms", "Find rooms", "Shapely", ["walls"], "done", f"{len(rooms)} rooms", t_rooms + 50),
    ])
    return Built(payload, trace, buf.getvalue(), ext_name)
