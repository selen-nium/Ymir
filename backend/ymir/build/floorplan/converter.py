"""Walls from the Stoa Lens PNG-to-IFC converter (research/stoa-lens/src/stoa_bim), as the app's segments.

The converter finds walls as thick dark ink after a morphological opening (`stoa_bim.generate.wall_mask`).
Line drawings are darkened first (clean.darken) so their double-line walls become solid bands. The
mask's outline is then turned into centreline segments by pairing each wall's two opposite faces,
at any angle, so diagonal walls survive (the old axis-aligned vectorizer dropped them).
"""
from __future__ import annotations

import math
import sys
import time
from functools import lru_cache

import cv2
import numpy as np

from ...settings import ROOT
from . import clean

RESEARCH_SRC = ROOT / "research" / "stoa-lens" / "src"
LINE_DRAWING_KERNEL_PX = 5  # wall-finder opening for darkened line drawings: drops strokes up to 4 px
OPEN_MIN_M, DOORWAY_MAX_M, OPEN_MAX_M = 0.55, 1.25, 3.6  # opening widths: narrowest, widest bare doorway, widest
BLOCK_MAX_THICK_M = 1.0  # thickest odd-shaped wall piece (pier, corner) built as a block


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
    """Doors and windows: the converter's gaps along horizontal and vertical wall runs (0.55-3.6 m),
    classified by the ink in them (glazing lines: window; a swing arc or a frame: door). Gaps with
    neither are left to bridge_collinear, which needs a wall on both sides."""
    G = _generate()
    out = []
    for rect, axis, thick in G.gaps(walls, {"crop": [int(v) for v in crop], "m_per_px": float(m_per_px)}):
        kind, _ = G.classify(rect, axis, thick, ink, walls)
        if kind in ("door", "window"):
            out.append({"kind": kind, "box": [round(float(v), 1) for v in rect.bounds]})
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
    # Face pairing stops where a face turns, short of a junction. Run each end on while it is still
    # inside wall, so walls reach into corners, T-junctions and piers and can be joined there.
    segs = [_grow(mask, a, e, t, reach=max_thick / 2) for a, e, t, _ in kept]
    segs = _merge_collinear(segs, sin_tol)
    segs = _join_ends([_straighten(*s) for s in segs], sin_tol)
    out = []
    for a, e, t in segs:
        if np.hypot(*(e - a)) >= max(min_len, 1.5 * t):
            out.append({"a": [round(float(a[0]), 1), round(float(a[1]), 1)],
                        "b": [round(float(e[0]), 1), round(float(e[1]), 1)], "thickness_px": round(float(t), 1)})
    return out


def _grow(mask: np.ndarray, a, e, t, reach: float):
    """Extend both ends along the wall while the centreline stays inside the mask, up to `reach` px."""
    h, w = mask.shape
    u = (e - a) / max(np.hypot(*(e - a)), 1e-9)
    ends = []
    for p, sign in ((a, -1.0), (e, 1.0)):
        n = 0
        while n < reach:
            q = p + sign * u * (n + 1)
            x, y = int(round(q[0])), int(round(q[1]))
            if not (0 <= x < w and 0 <= y < h) or not mask[y, x]:
                break
            n += 1
        ends.append(p + sign * u * n)
    return [ends[0], ends[1], t]


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


def _crosses(p, q, a, b) -> bool:
    """Do segments p-q and a-b properly intersect?"""
    def side(o, s, t):
        return (s[0] - o[0]) * (t[1] - o[1]) - (s[1] - o[1]) * (t[0] - o[0])
    d1, d2, d3, d4 = side(p, q, a), side(p, q, b), side(a, b, p), side(a, b, q)
    return d1 * d2 < 0 and d3 * d4 < 0


def _gap_kind(thin: np.ndarray, p, q, thick: float) -> str | None:
    """Like the converter's rule, at any angle: thin lines running along the gap between the two wall
    ends. Three or more (frame and glazing): window. One or two (a frame): door. None: nothing drawn."""
    h, w = thin.shape
    g = math.hypot(q[0] - p[0], q[1] - p[1])
    ux, uy = (q[0] - p[0]) / g, (q[1] - p[1]) / g
    along = np.arange(0.1 * g, 0.9 * g, 1.0)
    rows = []
    for o in np.arange(-thick / 2, thick / 2 + 0.5, 1.0):
        xs = np.rint(p[0] + ux * along - uy * o).astype(int)
        ys = np.rint(p[1] + uy * along + ux * o).astype(int)
        ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
        rows.append(bool(ok.any()) and float(thin[ys[ok], xs[ok]].mean()) >= 0.7)
    runs = sum(1 for i, r in enumerate(rows) if r and (i == 0 or not rows[i - 1]))
    return "window" if runs >= 3 else "door" if runs else None


def bridge_collinear(walls: list[dict], thin: np.ndarray, m_per_px: float, angle_tol_deg: float = 5.0) -> tuple[list[dict], list[dict]]:
    """Join walls on one line, at any angle, across the gap between their facing ends.

    Under the narrowest opening: one wall (a junction or a break in the drawing). Up to the widest
    opening, with nothing crossing: a window or door by the lines drawn in the gap, else a bare
    doorway if it is no wider than a door. The wall then runs through, and the opening is returned.
    Wider bare gaps stay open."""
    lo, doorway, hi = OPEN_MIN_M / m_per_px, DOORWAY_MAX_M / m_per_px, OPEN_MAX_M / m_per_px
    sin_tol = math.sin(math.radians(angle_tol_deg))
    cands = []
    for i, w in enumerate(walls):
        ax, ay = w["a"]; bx, by = w["b"]
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-6:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        for j in range(i + 1, len(walls)):
            v = walls[j]
            cx, cy = v["a"]; dx, dy = v["b"]
            lv = math.hypot(dx - cx, dy - cy)
            if lv < 1e-6 or abs(ux * (dy - cy) - uy * (dx - cx)) >= sin_tol * lv:
                continue
            tol = max(w["thickness_px"], v["thickness_px"]) / 2 + 2
            if abs(-(cx - ax) * uy + (cy - ay) * ux) > tol or abs(-(dx - ax) * uy + (dy - ay) * ux) > tol:
                continue
            sc, sd = (cx - ax) * ux + (cy - ay) * uy, (dx - ax) * ux + (dy - ay) * uy
            (s0, p0), (s1, p1) = sorted(((sc, v["a"]), (sd, v["b"])))
            if s0 >= length:
                gap, p, q = s0 - length, w["b"], p0
            elif s1 <= 0:
                gap, p, q = -s1, w["a"], p1
            else:
                continue
            if gap <= hi:
                cands.append((gap, i, j, p, q))
    root = list(range(len(walls)))
    live = [dict(w) for w in walls]
    alive = [True] * len(walls)
    found = []

    def find(i):
        while root[i] != i:
            i = root[i]
        return i

    for gap, i, j, p, q in sorted(cands, key=lambda c: c[0]):
        i, j = find(i), find(j)
        if i == j:
            continue
        w, v = live[i], live[j]
        thick = max(w["thickness_px"], v["thickness_px"])
        kind = None
        if gap >= lo:
            if any(alive[k] and k not in (i, j) and _crosses(p, q, live[k]["a"], live[k]["b"]) for k in range(len(live))):
                continue
            kind = _gap_kind(thin, p, q, max(6.0, thick))
            if kind is None:
                if gap > doorway:
                    continue
                kind = "passage"
        # One wall from the two: the farthest pair of their four ends.
        pts = [w["a"], w["b"], v["a"], v["b"]]
        a, b = max(((s, t) for s in pts for t in pts), key=lambda st: math.dist(*st))
        lw, lv = math.dist(w["a"], w["b"]), math.dist(v["a"], v["b"])
        live[i] = {**w, "a": list(a), "b": list(b), "thickness_px": round((w["thickness_px"] * lw + v["thickness_px"] * lv) / max(lw + lv, 1e-9), 1)}
        alive[j] = False
        root[j] = i
        if kind:
            x0, x1 = sorted((p[0], q[0])); y0, y1 = sorted((p[1], q[1]))
            if x1 - x0 < thick:
                x0, x1 = (x0 + x1 - thick) / 2, (x0 + x1 + thick) / 2
            if y1 - y0 < thick:
                y0, y1 = (y0 + y1 - thick) / 2, (y0 + y1 + thick) / 2
            found.append({"kind": kind, "box": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]})
    return [w for k, w in enumerate(live) if alive[k]], found


def wall_blocks(mask: np.ndarray, walls: list[dict], m_per_px: float) -> list[dict]:
    """Odd-shaped wall the segments don't cover (piers, splayed corners, curved pieces), as outlines
    to extrude. Pieces thicker than a wall can be (filled tables, furniture) are left out."""
    cover = np.zeros(mask.shape, np.uint8)
    for w in walls:
        cv2.line(cover, tuple(int(round(v)) for v in w["a"]), tuple(int(round(v)) for v in w["b"]), 1, max(1, int(round(w["thickness_px"]))))
    rest = mask & ~cv2.dilate(cover, np.ones((5, 5), np.uint8)).astype(bool)
    rest = cv2.morphologyEx(rest.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    min_area, max_thick = max(40.0, (0.2 / m_per_px) ** 2), BLOCK_MAX_THICK_M / m_per_px
    n, lab, stats, _ = cv2.connectedComponentsWithStats(rest, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_area:
            continue
        piece = np.pad((lab[y:y + bh, x:x + bw] == i).astype(np.uint8), 1)
        if cv2.distanceTransform(piece, cv2.DIST_L2, 3).max() * 2 > max_thick:
            continue
        contours, _ = cv2.findContours(piece, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        outline = cv2.approxPolyDP(max(contours, key=cv2.contourArea), 1.5, True)[:, 0, :]
        if len(outline) >= 3:
            out.append({"polygon": [[round(float(px + x - 1 + 0.5), 1), round(float(py + y - 1 + 0.5), 1)] for px, py in outline]})
    return out


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
    # The converter's gap scan only follows horizontal and vertical runs; do the same at any angle.
    walls, more = bridge_collinear(walls, ink & ~mask, m_per_px)
    found += more
    blocks = wall_blocks(mask, walls, m_per_px)
    for w in walls:
        w.update(status="proposed", method="stoa_bim", thickness_px=min(w["thickness_px"], round(thick, 1)))
    return walls, {"method": "Stoa Lens PNG-to-IFC wall mask + face pairing", "seconds": round(time.perf_counter() - t, 3),
                   "proposal_count": len(walls), "line_drawing": line_drawing, "vectorized": True, "openings": found, "blocks": blocks,
                   "limitations": ["Walls are thick ink after a morphological opening; furniture drawn as heavy blocks can read as wall.",
                                   "Line drawings are darkened and their double-line walls filled first.",
                                   "Doors and windows are gaps between wall pieces, classified by the ink in them; scale is estimated.",
                                   "Curved walls come out as straight pieces."]}
