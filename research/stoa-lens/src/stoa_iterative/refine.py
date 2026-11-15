"""Sharper room outlines for the push to 80% (PROGRESS: next phase).

`snap_bridged`: like stoa_semseg.geometry.snap, with two changes found by looking at rooms that end up 2-10% too
large after snap:
  1. door and window openings are bridged before flooding: the thick-ink wall mask is closed along its own
     direction (horizontal and vertical line kernels up to `gap_m` long), so the flood stops at the wall face
     instead of bulging into the opening;
  2. only holes smaller than `max_hole_m2` are filled (text, fixture symbols); shafts and voids stay holes.
Both use the drawing scale (true m/px here; a PNG-only system would read it from the sheet).
`regularize`: mitred morphological opening that removes protrusions thinner than `bump_m`.
"""
import cv2
import numpy as np
from scipy import ndimage as ndi
from shapely.geometry import Polygon

from stoa_semseg import geometry as sg


def wall_barrier(rgb, m_per_px, gap_m=1.2):
    free = sg.free_space(rgb)
    ink = ~free
    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    walls = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, k3, iterations=2)
    L = max(3, int(round(gap_m / m_per_px)))
    h = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
    v = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, L)))
    return free, ink | h.astype(bool) | v.astype(bool)


def snap_bridged(instances, rgb, offset, m_per_px, grow=1.0, gap_m=1.2, max_hole_m2=0.3, erode=3, band_px=8):
    from skimage.segmentation import watershed
    rgb = np.asarray(rgb)
    free, barrier = wall_barrier(rgb, m_per_px, gap_m)
    room_free = ~barrier
    H, W = free.shape
    order = sorted(range(len(instances)), key=lambda k: instances[k]['score'])
    painted = np.zeros((H, W), np.int32)
    for k in order:
        painted[sg.rasterize(instances[k]['polygons'], offset, (H, W))] = k + 1
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode + 1,) * 2)
    markers = np.zeros((H, W), np.int32)
    for k in range(len(instances)):
        markers[cv2.erode((painted == k + 1).astype(np.uint8), ker).astype(bool) & room_free] = k + 1
    band = cv2.dilate((painted > 0).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_RECT, (2 * band_px + 1,) * 2)).astype(bool)
    lab = watershed(np.zeros((H, W), np.uint8), markers, mask=room_free & band)
    max_hole = max_hole_m2 / (m_per_px ** 2)
    kc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    out = []
    for k, inst in enumerate(instances):
        m = (lab == k + 1).astype(np.uint8)
        if not m.any():
            continue
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kc).astype(bool) & band
        holes = ndi.binary_fill_holes(m) & ~m
        hl, n = ndi.label(holes)
        if n:
            sizes = ndi.sum(np.ones_like(hl), hl, index=np.arange(1, n + 1))
            small = np.isin(hl, np.where(sizes <= max_hole)[0] + 1)
            m = m | small
        polys = sg.mask_to_polygons(m, offset, grow=grow)
        if polys:
            out.append({**inst, 'polygons': polys})
    return out


def regularize(instances, m_per_px, bump_m=0.4, max_change=0.15):
    k = bump_m / m_per_px
    out = []
    for i in instances:
        g = Polygon(i['polygons'][0]).buffer(0)
        r = g.buffer(-k / 2, join_style=2).buffer(k / 2, join_style=2)
        parts = [x for x in getattr(r, 'geoms', [r]) if x.geom_type == 'Polygon' and not x.is_empty]
        if parts:
            r = max(parts, key=lambda x: x.area)
            if abs(r.area - g.area) <= max_change * g.area:
                g = r
        if g.geom_type == 'Polygon':
            out.append({**i, 'polygons': [list(g.exterior.coords)[:-1]]})
    return out
