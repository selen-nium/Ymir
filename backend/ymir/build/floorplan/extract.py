"""Fast local raster wall proposals. Always unreviewed; no semantic accuracy claim."""
from __future__ import annotations

import hashlib
import io
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


def normalize(raw: bytes) -> tuple[bytes, dict]:
    with Image.open(io.BytesIO(raw)) as original:
        if original.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("Use a PNG, JPEG or WebP floorplan image.")
        if original.width * original.height > 30_000_000:
            raise ValueError("Image exceeds 30 megapixels. Crop or resize it first.")
        image = ImageOps.exif_transpose(original).convert("RGBA")
        white = Image.new("RGBA", image.size, "white")
        white.alpha_composite(image)
        image = white.convert("RGB")
        original_size = image.size
        image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
        if min(image.size) < 100:
            raise ValueError("Image is too small. Use at least 100 pixels in each direction.")
        out = io.BytesIO()
        image.save(out, format="PNG")
        png = out.getvalue()
    return png, {"original_sha256": hashlib.sha256(raw).hexdigest(),
                 "normalized_sha256": hashlib.sha256(png).hexdigest(),
                 "width": image.width, "height": image.height,
                 "original_width": original_size[0], "original_height": original_size[1],
                 "coordinate_frame": "normalized PNG pixels; x right, y down",
                 "normalization": "EXIF orientation, white alpha composite, max side 2200; no generative cleanup"}


LOW_CONFIDENCE = 0.85  # flag walls below this mean wall probability (benchmark: ~10 flags/plan catch ~73% of false walls)


def _propose_wallnet(png: bytes, crop) -> tuple[list[dict], dict]:
    """Learned wall segmentation -> joined axis-aligned segments with confidence and thickness."""
    from . import vectorize, wallnet
    t = time.perf_counter()
    bgr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    x0, y0, x1, y1 = map(int, crop)
    roi = bgr[y0:y1, x0:x1]
    if not roi.size:
        raise ValueError("Empty recognition region")
    prob = wallnet.predict(roi)
    segs = vectorize.clean(vectorize.segments_from_mask((prob > .5).astype(np.uint8) * 255, prob))
    digest = hashlib.sha256(png).hexdigest()
    walls = []
    for s in sorted(segs, key=lambda s: -((s["b"][0]-s["a"][0])**2+(s["b"][1]-s["a"][1])**2))[:300]:
        a, b = [s["a"][0]+x0, s["a"][1]+y0], [s["b"][0]+x0, s["b"][1]+y0]
        walls.append({"id": "wall-"+hashlib.sha256((digest+str(a)+str(b)).encode()).hexdigest()[:12],
                      "a": a, "b": b, "status": "proposed", "method": "wallnet",
                      "confidence": round(min(1.0, max(0.0, s["confidence"])), 3),
                      "thickness_px": round(min(500.0, max(0.0, s["thickness_px"])), 2)})
    low = sum(1 for w in walls if w["confidence"] < LOW_CONFIDENCE)
    return walls, {"method": "learned wall segmentation (U-Net, CubiCasa5K-trained) + axis vectorizer",
                   "seconds": round(time.perf_counter()-t, 3), "proposal_count": len(walls),
                   "low_confidence": low, "truncated": len(segs) > 300,
                   "limitations": ["Trained on CubiCasa5K residential plans; other drawing styles may need fine-tuning.",
                                   "Only horizontal/vertical walls are vectorized; diagonal and curved walls need manual tracing.",
                                   "Openings are not detected yet; walls run through doors and windows.",
                                   "Confidence is the mean wall probability along each segment, not a calibrated accuracy."]}


def propose(png: bytes, crop, minimum_px=70, merge_px=10, detector: str = "lines") -> tuple[list[dict], dict]:
    """detector: 'lines' (classical), 'wallnet' (learned; requires weights) or 'auto' (wallnet if available)."""
    if detector in ("auto", "wallnet"):
        from . import wallnet
        if wallnet.available():
            return _propose_wallnet(png, crop)
        if detector == "wallnet":
            raise ValueError("Wall model weights are not installed (weights/wallnet.pt).")
    t = time.perf_counter()
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
    x0, y0, x1, y1 = map(int, crop)
    roi = image[y0:y1, x0:x1]
    if not roi.size:
        raise ValueError("Empty recognition region")
    minimum_px = max(25, min(500, int(minimum_px)))
    merge_px = max(1, min(35, int(merge_px)))
    _, binary = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    candidates = []
    for horizontal in (True, False):
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (minimum_px, 1) if horizontal else (1, minimum_px))
        opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        count, _, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
        segments = []
        for x, y, w, h, area in stats[1:count]:
            length, thick = (w, h) if horizontal else (h, w)
            if length < minimum_px or thick > min(50, min(roi.shape)*.09):
                continue
            lo, hi = (x, x+w-1) if horizontal else (y, y+h-1)
            centre = y+(h-1)/2 if horizontal else x+(w-1)/2
            segments.append([float(lo), float(hi), float(centre), int(thick)])
        # Merge nearby parallel strokes only with substantial mutual overlap.
        # Uncertain axes remain proposals; do not use this as semantic wall validation.
        segments.sort(key=lambda s: (s[2], s[0]))
        used = set()
        for i, seg in enumerate(segments):
            if i in used:
                continue
            group = [seg]
            used.add(i)
            for j, other in enumerate(segments):
                if j in used or abs(other[2]-seg[2]) > merge_px:
                    continue
                overlap = min(seg[1], other[1])-max(seg[0], other[0])
                if overlap > .7*min(seg[1]-seg[0], other[1]-other[0]):
                    group.append(other); used.add(j)
            lo = min(s[0] for s in group); hi = max(s[1] for s in group)
            centre = (min(s[2] for s in group)+max(s[2] for s in group))/2
            a, b = ([lo+x0, centre+y0], [hi+x0, centre+y0]) if horizontal else ([centre+x0, lo+y0], [centre+x0, hi+y0])
            candidates.append({"a": a, "b": b, "status": "proposed", "method": "raster_lines"})
    candidates.sort(key=lambda c: -((c["b"][0]-c["a"][0])**2+(c["b"][1]-c["a"][1])**2))
    truncated = len(candidates)>150
    candidates = candidates[:150]
    digest = hashlib.sha256(png).hexdigest()
    for c in candidates:
        c["id"] = "wall-"+hashlib.sha256((digest+str(c["a"])+str(c["b"])).encode()).hexdigest()[:12]
    return candidates, {"method": "axis-aligned raster line proposals", "seconds": round(time.perf_counter()-t, 3),
                        "proposal_count": len(candidates), "truncated": truncated,
                        "limitations": ["No learned wall/door/window classifier or VLM is connected.",
                                        "Furniture, dimensions and hatching can become false proposals.",
                                        "Diagonal/curved walls and openings require manual tracing.",
                                        "Image distortion is not automatically corrected. Rectify externally if calibration disagrees."]}


def demo_image() -> bytes:
    image = Image.new("RGB", (1000, 720), "#fafbf7")
    draw = ImageDraw.Draw(image)
    # Centreline rectangle is exactly 600 × 400 px: 12 × 8 m at 0.02 m/px.
    for a, b in [((180, 160), (780, 160)), ((780, 160), (780, 560)),
                 ((780, 560), (180, 560)), ((180, 560), (180, 160)),
                 ((480, 160), (480, 560)), ((180, 360), (480, 360))]:
        draw.line([a, b], fill="#293d35", width=10)
    draw.text((180, 52), "NORTH QUAY / ENGINEERING FLOORPLAN", fill="#3a5145")
    draw.text((180, 74), "SYNTHETIC TRAINING EXAMPLE - NOT A REAL PROJECT", fill="#9a6e36")
    draw.text((430, 115), "12.00 m (wall centreline to centreline)", fill="#55705b")
    draw.text((810, 350), "8.00 m", fill="#55705b")
    draw.text((280, 245), "SPACE 01", fill="#6e7b67")
    draw.text((280, 445), "SPACE 02", fill="#6e7b67")
    draw.text((600, 345), "SPACE 03", fill="#6e7b67")
    draw.text((180, 635), "Calibration points: (180,160) to (780,160) = 12 m", fill="#6e7b67")
    draw.text((180, 655), "Independent check: (780,160) to (780,560) = 8 m", fill="#6e7b67")
    out = io.BytesIO(); image.save(out, "PNG")
    return out.getvalue()
