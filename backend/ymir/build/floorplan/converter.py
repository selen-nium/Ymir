"""Walls from the Stoa Lens PNG-to-IFC converter (research/stoa-lens/src/stoa_bim), as the app's segments.

The converter finds walls as thick dark ink after a morphological opening (`stoa_bim.generate.wall_mask`).
Line drawings are darkened first (clean.darken) so their double-line walls become solid bands. The
mask's outline is then turned into centreline segments by pairing each wall's two opposite faces,
at any angle, so diagonal walls survive (the old axis-aligned vectorizer dropped them).
"""
from __future__ import annotations

import sys
import time
from functools import lru_cache

import cv2
import numpy as np

from ...settings import ROOT
from . import clean

RESEARCH_SRC = ROOT / "research" / "stoa-lens" / "src"
LINE_DRAWING_KERNEL_PX = 5  # wall-finder opening for darkened line drawings: drops strokes up to 4 px


def available() -> bool:
    return (RESEARCH_SRC / "stoa_bim" / "generate.py").is_file()


@lru_cache(maxsize=1)
def _generate():
    if str(RESEARCH_SRC) not in sys.path:
        sys.path.insert(0, str(RESEARCH_SRC))
    from stoa_bim import generate
    return generate


def wall_mask(bgr: np.ndarray, crop, m_per_px: float) -> tuple[np.ndarray, np.ndarray]:
    """The converter's wall pixels and its ink mask (bool, image size), searched inside `crop`."""
    rgb = bgr[..., ::-1].astype(np.int16)
    gray, sat = rgb.mean(2), rgb.max(2) - rgb.min(2)
    return _generate().wall_mask({"crop": [int(v) for v in crop], "m_per_px": float(m_per_px)}, gray, sat, [])


def openings(walls: np.ndarray, ink: np.ndarray, crop, m_per_px: float) -> list[dict]:
    """Doors and windows: the converter's gaps along wall runs (0.55-3.6 m), classified by the ink in
    them (glazing lines: window; a swing arc or a frame: door). Open passages are left out."""
    G = _generate()
    out = []
    for rect, axis, thick in G.gaps(walls, {"crop": [int(v) for v in crop], "m_per_px": float(m_per_px)}):
        kind, _ = G.classify(rect, axis, thick, ink, walls)
        if kind in ("door", "window"):
            out.append({"kind": kind, "axis": axis, "box": [round(float(v), 1) for v in rect.bounds]})
    return out


def _edges(mask: np.ndarray, min_len: float):
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    p, q = [], []
    for c in contours:
        if len(c) < 4:
            continue
        v = cv2.approxPolyDP(c, 1.5, True)[:, 0, :].astype(np.float64) + 0.5
        for a, b in zip(v, np.roll(v, -1, axis=0)):
            if np.hypot(*(b - a)) >= min_len:
                p.append(a); q.append(b)
    return np.array(p).reshape(-1, 2), np.array(q).reshape(-1, 2)


def _inside(mask: np.ndarray, a, b, n=7) -> float:
    h, w = mask.shape
    t = np.linspace(0.1, 0.9, n)[:, None]
    pts = np.rint(a + (b - a) * t).astype(int)
    ok = (pts[:, 0] >= 0) & (pts[:, 0] < w) & (pts[:, 1] >= 0) & (pts[:, 1] < h)
    return float(mask[pts[ok, 1], pts[ok, 0]].mean()) if ok.any() else 0.0


def segments_from_wall_mask(mask: np.ndarray, *, max_thick: float, min_thick: float = 2.0,
                            min_len: float = 10.0, angle_tol_deg: float = 5.0) -> list[dict]:
    """Centreline segments {a, b, thickness_px} from a wall mask: each pair of nearly parallel outline
    edges at most `max_thick` apart, overlapping along their length, with wall between them."""
    P, Q = _edges(mask, min_len)
    if not len(P):
        return []
    D = Q - P
    L = np.hypot(D[:, 0], D[:, 1])
    U = D / L[:, None]
    N = np.stack([-U[:, 1], U[:, 0]], 1)
    theta = np.mod(np.arctan2(U[:, 1], U[:, 0]), np.pi)
    bins = 60
    b_of = (theta / np.pi * bins).astype(int) % bins
    sin_tol = np.sin(np.radians(angle_tol_deg))
    cands = []
    for b in range(bins):
        I = np.nonzero(b_of == b)[0]
        if not len(I):
            continue
        J = np.nonzero(np.isin(b_of, [(b - 1) % bins, b, (b + 1) % bins]))[0]
        for i in I:
            j = J[J > i]
            if not len(j):
                continue
            parallel = np.abs(U[i, 0] * U[j, 1] - U[i, 1] * U[j, 0]) < sin_tol
            mid = (P[j] + Q[j]) / 2
            d = (mid - P[i]) @ N[i]
            s0, s1 = (P[j] - P[i]) @ U[i], (Q[j] - P[i]) @ U[i]
            lo, hi = np.maximum(0, np.minimum(s0, s1)), np.minimum(L[i], np.maximum(s0, s1))
            ok = parallel & (np.abs(d) >= min_thick) & (np.abs(d) <= max_thick) & (hi - lo >= min_len)
            for k in np.nonzero(ok)[0]:
                a = P[i] + U[i] * lo[k] + N[i] * d[k] / 2
                e = P[i] + U[i] * hi[k] + N[i] * d[k] / 2
                if _inside(mask, a, e) >= 0.8:
                    cands.append((a, e, abs(float(d[k]))))
    # Longest first; a candidate mostly along an accepted wall extends it instead of doubling it.
    cands.sort(key=lambda c: -np.hypot(*(c[1] - c[0])))
    kept: list[list] = []
    for a, e, t in cands:
        u = (e - a) / max(np.hypot(*(e - a)), 1e-9)
        for w in kept:
            wa, we, wt, wu = w
            if abs(u[0] * wu[1] - u[1] * wu[0]) >= sin_tol:
                continue
            n = np.array([-wu[1], wu[0]])
            if abs(((a + e) / 2 - wa) @ n) > max(t, wt) / 2 + 1:
                continue
            sa, se = sorted(((a - wa) @ wu, (e - wa) @ wu))
            length = np.hypot(*(we - wa))
            if min(se, length) - max(sa, 0) >= 0.6 * (se - sa):
                if sa < 0:
                    w[0] = wa + wu * sa
                if se > length:
                    w[1] = wa + wu * se
                break
        else:
            kept.append([a, e, t, u])
    segs = _merge_collinear([[a, e, t] for a, e, t, _ in kept], sin_tol)
    segs = _join_ends([_straighten(*s) for s in segs], sin_tol)
    out = []
    for a, e, t in segs:
        if np.hypot(*(e - a)) >= max(min_len, 1.5 * t):
            out.append({"a": [round(float(a[0]), 1), round(float(a[1]), 1)],
                        "b": [round(float(e[0]), 1), round(float(e[1]), 1)], "thickness_px": round(float(t), 1)})
    return out


def _straighten(a, e, t, tol=0.02):
    """Walls within about a degree of horizontal or vertical are made exactly so; outline jitter
    otherwise tilts them by a pixel over hundreds."""
    d = e - a
    if abs(d[0]) < tol * abs(d[1]):
        x = (a[0] + e[0]) / 2
        a, e = np.array([x, a[1]]), np.array([x, e[1]])
    elif abs(d[1]) < tol * abs(d[0]):
        y = (a[1] + e[1]) / 2
        a, e = np.array([a[0], y]), np.array([e[0], y])
    return [a, e, t]


def _merge_collinear(segs: list[list], sin_tol: float) -> list[list]:
    """Join pieces of one wall split by a gap narrower than the wall is thick (a junction, a bit of
    noise). Door-sized gaps are wider and stay open."""
    merged = True
    while merged:
        merged = False
        for i in range(len(segs)):
            a, e, t = segs[i]
            u = (e - a) / max(np.hypot(*(e - a)), 1e-9)
            n = np.array([-u[1], u[0]])
            for j in range(i + 1, len(segs)):
                b, f, s = segs[j]
                v = (f - b) / max(np.hypot(*(f - b)), 1e-9)
                if abs(u[0] * v[1] - u[1] * v[0]) >= sin_tol or abs(((b + f) / 2 - a) @ n) > max(t, s) / 2 + 1:
                    continue
                length = np.hypot(*(e - a))
                sb, sf = sorted(((b - a) @ u, (f - a) @ u))
                if sb > length + max(t, s) or sf < -max(t, s):
                    continue
                lo, hi = min(0.0, sb), max(length, sf)
                lb = np.hypot(*(f - b))
                segs[i] = [a + u * lo, a + u * hi, (t * length + s * lb) / max(length + lb, 1e-9)]
                del segs[j]
                merged = True
                break
            if merged:
                break
    return segs


def _join_ends(segs: list[list], sin_tol: float) -> list[list]:
    """Snap each free end to where its wall's line meets a nearby wall's line, extending or trimming
    either wall, so corners and T-junctions close and rooms can form."""
    for i in range(len(segs)):
        for end in (0, 1):
            a, e, t = segs[i]
            p = (a, e)[end]
            d = e - a
            if np.hypot(*d) < 1e-6:
                continue
            best = None
            for j in range(len(segs)):
                if j == i:
                    continue
                b, f, s = segs[j]
                g = f - b
                lj = np.hypot(*g)
                if lj < 1e-6:
                    continue
                den = d[0] * g[1] - d[1] * g[0]
                if abs(den) < sin_tol * np.hypot(*d) * np.hypot(*g):
                    continue  # parallel
                r = b - a
                x = a + d * ((r[0] * g[1] - r[1] * g[0]) / den)
                tol = max(t, s) + 4
                along = (x - b) @ (g / lj)
                gap = np.hypot(*(x - p))
                if gap <= tol and -tol <= along <= lj + tol and (best is None or gap < best[0]):
                    best = (gap, j, x, along)
            if best is None:
                continue
            _, j, x, along = best
            # Half a pixel past the meeting point, so the two centrelines cross and the room finder
            # nodes the junction (a point exactly on a line is lost to rounding).
            x = x + d / np.hypot(*d) * (0.5 if end else -0.5)
            if end:
                segs[i][1] = x
            else:
                segs[i][0] = x
            b, f, s = segs[j]  # a T into the end of another wall extends that wall too
            if along < 0:
                segs[j][0] = x
            elif along > np.hypot(*(f - b)):
                segs[j][1] = x
    return segs


def propose(png: bytes, crop, m_per_px: float) -> tuple[list[dict], dict]:
    """Wall proposals in image pixels, like extract.propose."""
    t = time.perf_counter()
    bgr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    x0, y0, x1, y1 = (int(v) for v in crop)
    gray = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    if not gray.size:
        raise ValueError("Empty recognition region")
    line_drawing = clean.is_line_drawing(gray)
    if line_drawing:
        # Darkened walls are solid bands; set the converter's opening (0.06 m / m_per_px) to
        # LINE_DRAWING_KERNEL_PX so the remaining thin lines drop out.
        bgr, finder_m = clean.darken(bgr), 0.06 / LINE_DRAWING_KERNEL_PX
    else:
        finder_m = m_per_px
    mask, ink = wall_mask(bgr, crop, finder_m)
    found = openings(mask, ink, crop, m_per_px)
    # A wall runs through its doors and windows: fill each opening so the wall stays one piece, rooms
    # close, and the opening has a wall to sit in.
    closed = mask.copy()
    for o in found:
        x0o, y0o, x1o, y1o = (int(round(v)) for v in o["box"])
        closed[y0o:y1o, x0o:x1o] = True
    thick = max(16.0, 0.8 / m_per_px)
    # A wall drawn with a window strip or several parallel lines fills into a band wider than any real
    # wall. Pair across up to twice the limit so it is not lost, but report a plausible thickness.
    walls = segments_from_wall_mask(closed, max_thick=2 * thick)
    for w in walls:
        w.update(status="proposed", method="stoa_bim", thickness_px=min(w["thickness_px"], round(thick, 1)))
    return walls, {"method": "Stoa Lens PNG-to-IFC wall mask + face pairing", "seconds": round(time.perf_counter() - t, 3),
                   "proposal_count": len(walls), "line_drawing": line_drawing, "vectorized": True, "openings": found,
                   "limitations": ["Walls are thick ink after a morphological opening; furniture drawn as heavy blocks can read as wall.",
                                   "Line drawings are darkened and their double-line walls filled first.",
                                   "Doors and windows are gaps between wall pieces, classified by the ink in them; scale is estimated.",
                                   "Curved walls come out as straight pieces."]}
