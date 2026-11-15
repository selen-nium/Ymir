"""Read an IFC back and draw its 2D plan view (a horizontal section, as floor plans are drawn) in the PNG pixel frame.

This works from the IFC file alone: IfcOpenShell tessellates each product's 3D body in world coordinates. The mesh
is cut by the plane z = CUT, and the cut segments are closed into polygons:
  - IfcWall, IfcColumn: cut polygons filled (walls are drawn solid in plan)
  - IfcDoor, IfcWindow, IfcFurnishingElement: cut outlines drawn as lines
  - IfcDoor 'Annotation' Curve2D items (leaf, swing): drawn as lines
  - IfcWindow: a centre line along its long axis (plans draw glazing)
The caller maps metres to pixels with the frame's m_per_px (x = X / k, y = -Y / k).
"""
import cv2
import numpy as np
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.ops import polygonize, unary_union

CUT = 1.2  # m above storey level


def _cut(verts, faces, z):
    """Segments where the plane z cuts the triangles."""
    segs = []
    v = verts
    for a, b, c in faces:
        P = v[[a, b, c]]
        d = P[:, 2] - z
        pts = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            if (d[i] > 0) != (d[j] > 0):
                t = d[i] / (d[i] - d[j])
                pts.append(P[i, :2] + t * (P[j, :2] - P[i, :2]))
        if len(pts) == 2:
            segs.append((tuple(pts[0]), tuple(pts[1])))
    return segs


def section(ifc_path, z=CUT):
    """{class: [shapely geometry]} of the plan section of every product, plus annotation curves, in metres."""
    import ifcopenshell
    import ifcopenshell.geom
    f = ifcopenshell.open(str(ifc_path))
    st = ifcopenshell.geom.settings()
    st.set('use-world-coords', True)
    out = {}
    for e in f.by_type('IfcProduct'):
        cls = e.is_a()
        if cls not in ('IfcWall', 'IfcWallStandardCase', 'IfcColumn', 'IfcDoor', 'IfcWindow', 'IfcFurnishingElement', 'IfcSanitaryTerminal', 'IfcStair') or not e.Representation:
            continue
        key = 'IfcWall' if cls.startswith('IfcWall') or cls == 'IfcColumn' else cls
        try:
            sh = ifcopenshell.geom.create_shape(st, e)
            v = np.array(sh.geometry.verts).reshape(-1, 3)
            t = np.array(sh.geometry.faces).reshape(-1, 3)
        except Exception:
            v, t = np.zeros((0, 3)), np.zeros((0, 3), int)
        if len(t) and cls not in ('IfcSanitaryTerminal', 'IfcStair'):  # these are drawn by their plan curves
            zc = z if v[:, 2].max() > z else (v[:, 2].min() + v[:, 2].max()) / 2  # below the cut: drawn as seen from above
            segs = _cut(v, t, zc)
            if segs:
                q = lambda pt: (round(pt[0], 5), round(pt[1], 5))  # join endpoints computed from different triangles
                lines = unary_union([LineString([q(a), q(b)]) for a, b in segs if q(a) != q(b)])
                polys = list(polygonize(lines))
                g = unary_union(polys) if polys else MultiLineString(segs)
                out.setdefault(key, []).append(g)
        for rep in e.Representation.Representations:
            if rep.RepresentationIdentifier in ('Annotation', 'Plan', 'FootPrint') and rep.RepresentationType in ('Curve2D', 'GeometricCurveSet'):
                for it in rep.Items:
                    if it.is_a('IfcPolyline'):
                        out.setdefault(key + ':plan', []).append(LineString([p.Coordinates[:2] for p in it.Points]))
    return out


def rasterize(sec, frame, line_px=2):
    """Plan section → uint8 ink mask (1 = ink) in the PNG pixel frame, plus per-class masks."""
    k = frame['m_per_px']
    H, W = frame['height'], frame['width']
    S = 4  # sub-pixel shift for cv2

    def pts(coords):
        a = np.asarray(coords, float)[:, :2]
        return np.round(np.stack([a[:, 0] / k, -a[:, 1] / k], 1) * (1 << S)).astype(np.int32)
    masks = {}
    for cls, geoms in sec.items():
        m = np.zeros((H, W), np.uint8)
        fill = cls == 'IfcWall'
        for g in geoms:
            for p in getattr(g, 'geoms', [g]):
                if p.geom_type == 'Polygon':
                    rings = [pts(p.exterior.coords)] + [pts(h.coords) for h in p.interiors]
                    if fill:
                        cv2.fillPoly(m, rings[:1], 1, shift=S)
                        for h in rings[1:]:
                            cv2.fillPoly(m, [h], 0, shift=S)
                    cv2.polylines(m, rings, True, 1, line_px, shift=S)
                    if False:  # windows: glazing comes from the IFC's own plan curves
                        r = p.minimum_rotated_rectangle
                        c = list(r.exterior.coords)
                        e1, e2 = LineString(c[0:2]), LineString(c[1:3])
                        a, b = (c[1], c[2]) if e1.length > e2.length else (c[0], c[1])
                        mid1 = ((c[0][0] + a[0]) / 2, (c[0][1] + a[1]) / 2)
                        if e1.length > e2.length:
                            l = LineString([((c[0][0] + c[3][0]) / 2, (c[0][1] + c[3][1]) / 2), ((c[1][0] + c[2][0]) / 2, (c[1][1] + c[2][1]) / 2)])
                        else:
                            l = LineString([((c[0][0] + c[1][0]) / 2, (c[0][1] + c[1][1]) / 2), ((c[2][0] + c[3][0]) / 2, (c[2][1] + c[3][1]) / 2)])
                        cv2.polylines(m, [pts(l.coords)], False, 1, line_px, shift=S)
                elif p.geom_type == 'LineString':
                    cv2.polylines(m, [pts(p.coords)], False, 1, line_px, shift=S)
        masks[cls.split(':')[0]] = np.maximum(masks.get(cls.split(':')[0], 0), m) if cls.split(':')[0] in masks else m
    ink = np.zeros((H, W), np.uint8)
    for m in masks.values():
        ink |= m
    return ink, masks
