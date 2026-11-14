"""Canonical image-space geometry and conservative generation gates.

Coordinates always refer to the normalized source PNG, not the display canvas.
Nothing here silently snaps, rescales, removes, or repairs a user's geometry.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

Point = tuple[float, float]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Reference(Strict):
    a: Point
    b: Point
    length_m: float = Field(gt=0, le=1000)
    label: str = Field(default="Known dimension", max_length=120)


class Wall(Strict):
    id: str = Field(min_length=1, max_length=100)
    a: Point
    b: Point
    status: Literal["proposed", "accepted"] = "proposed"
    method: Literal["raster_lines", "wallnet", "human"] = "human"
    thickness_m: float | None = Field(default=None, ge=.03, le=2)
    # Detector evidence: mean wall probability along the segment, and drawn wall thickness in pixels.
    confidence: float | None = Field(default=None, ge=0, le=1)
    thickness_px: float | None = Field(default=None, ge=0, le=500)
    height_m: float | None = Field(default=None, ge=.3, le=20)


class Opening(Strict):
    id: str = Field(min_length=1, max_length=100)
    wall_id: str = Field(min_length=1, max_length=100)
    kind: Literal["door", "window"]
    # Centre position is explicitly measured along the chosen wall, from 0 to 1.
    t: float = Field(ge=0, le=1)
    width_m: float = Field(gt=0, le=20)
    height_m: float = Field(gt=0, le=20)
    sill_m: float = Field(default=0, ge=0, le=20)
    status: Literal["proposed", "accepted"] = "proposed"


class Plan(Strict):
    name: str = Field(default="Untitled floor", max_length=160)
    crop: tuple[float, float, float, float]
    references: list[Reference] = Field(default_factory=list, max_length=2)
    walls: list[Wall] = Field(default_factory=list, max_length=300)
    openings: list[Opening] = Field(default_factory=list, max_length=300)
    default_thickness_m: float = Field(default=.2, ge=.03, le=2)
    default_height_m: float = Field(default=3, ge=.3, le=20)
    elevation_m: float = Field(default=0, ge=-100, le=1000)
    vertical_basis: Literal["assumed", "user_supplied"] = "assumed"
    vertical_note: str = Field(default="", max_length=500)
    dimensions_confirmed: bool = False
    geometry_confirmed: bool = False
    reviewer: str = Field(default="", max_length=100)

    @field_validator("crop")
    @classmethod
    def crop_order(cls, v):
        if v[2] - v[0] < 20 or v[3] - v[1] < 20:
            raise ValueError("Recognition region must be at least 20 × 20 pixels")
        return v


def fingerprint(source: dict, plan: Plan) -> str:
    return hashlib.sha256(json.dumps({"source": source, "plan": plan.model_dump(),
                                     "parser_version": "0.1.0"}, sort_keys=True).encode()).hexdigest()


def distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def calibration(plan: Plan) -> dict:
    refs = plan.references
    if not refs:
        return {"scale_m_per_px": None, "passed": False, "message": "Mark a known dimension to establish scale."}
    lengths = [distance(r.a, r.b) for r in refs]
    if any(v < 20 for v in lengths):
        return {"scale_m_per_px": None, "passed": False, "message": "Use reference segments at least 20 pixels long."}
    scale = refs[0].length_m / lengths[0]
    out = {"scale_m_per_px": scale, "passed": False, "message": "Add a second known dimension in another direction."}
    if len(refs) < 2:
        return out
    u = [(refs[0].b[i] - refs[0].a[i]) / lengths[0] for i in range(2)]
    v = [(refs[1].b[i] - refs[1].a[i]) / lengths[1] for i in range(2)]
    angle = math.degrees(math.acos(min(1, abs(sum(x*y for x, y in zip(u, v))))))
    error = abs(lengths[1] * scale - refs[1].length_m) / refs[1].length_m
    out.update(check_error=error, direction_angle_deg=angle,
               check_predicted_m=lengths[1] * scale, check_declared_m=refs[1].length_m)
    if angle < 30:
        out["message"] = "Use a second reference at least 30° from the first to check directional distortion."
    elif error > .02:
        out["message"] = f"References disagree by {error:.1%}. Check endpoints, dimensions or image distortion."
    else:
        out.update(passed=True, message=f"Two references agree within {error:.1%}. Single-scale image check passed.")
    return out


def _drawn_thickness(w: Wall, scale: float) -> float | None:
    """Use the detected drawn thickness when it is plausible for a wall (5-60 cm)."""
    if not w.thickness_px:
        return None
    t = w.thickness_px * scale
    return round(t, 3) if .05 <= t <= .6 else None


def model_walls(plan: Plan, scale: float) -> list[dict]:
    x0, _, _, y1 = plan.crop
    return [{"id": w.id, "a": [(w.a[0]-x0)*scale, (y1-w.a[1])*scale],
             "b": [(w.b[0]-x0)*scale, (y1-w.b[1])*scale],
             "thickness_m": w.thickness_m or _drawn_thickness(w, scale) or plan.default_thickness_m,
             "height_m": w.height_m or plan.default_height_m,
             "elevation_m": plan.elevation_m, "source_a_px": w.a, "source_b_px": w.b,
             "method": w.method} for w in plan.walls if w.status == "accepted"]


DOOR_GAP_M = 1.15  # widest break in a wall line treated as a doorway when closing rooms


def room_polygons(walls: list[dict], bridges: list[dict] | None = None) -> list[dict]:
    """Rooms = faces of the wall-centreline network. bridges close door-sized breaks (see vectorize.bridge_gaps)."""
    if not walls:
        return []
    network = unary_union([LineString([w["a"], w["b"]]) for w in walls + (bridges or [])])
    polygons = sorted(polygonize(network), key=lambda p: (-p.area, p.centroid.x, p.centroid.y))
    return [{"id": f"space-{i+1}", "name": f"Space {i+1}",
             "polygon_m": [list(p) for p in poly.exterior.coords],
             "holes_m": [[list(p) for p in ring.coords] for ring in poly.interiors],
             "area_m2": poly.area, "area_basis": "wall_centrelines; not net usable floor area"}
            for i, poly in enumerate(polygons) if poly.area >= .25]


def validate(plan: Plan, width: int, height: int) -> dict:
    issues = []

    def issue(code, message, ids=None, severity="error"):
        issues.append({"code": code, "message": message, "element_ids": ids or [], "severity": severity})

    x0, y0, x1, y1 = plan.crop
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
        issue("crop", "The recognition region lies outside the source image.")
    all_ids = [w.id for w in plan.walls] + [o.id for o in plan.openings]
    if len(set(all_ids)) != len(all_ids):
        issue("duplicate_id", "Element IDs must be unique; duplicate geometry cannot be exported.")
    for w in plan.walls:
        for p in (w.a, w.b):
            if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1):
                issue("outside_scope", "Wall endpoint lies outside the selected region.", [w.id])
                break
    for r in plan.references:
        if any(not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1) for p in (r.a, r.b)):
            issue("reference_scope", "Calibration references must lie inside the selected region.")
    cal = calibration(plan)
    if not cal["passed"]:
        issue("calibration", cal["message"])
    scale = cal["scale_m_per_px"]
    pending = [w.id for w in plan.walls if w.status == "proposed"] + [o.id for o in plan.openings if o.status == "proposed"]
    if pending:
        issue("pending_review", f"Accept or remove {len(pending)} unreviewed element(s).", pending)
    if not any(w.status == "accepted" for w in plan.walls):
        issue("empty", "Accept or draw at least one wall.")
    if not plan.dimensions_confirmed:
        issue("dimensions", "Confirm wall thicknesses, heights and their source or assumption basis.")
    if plan.vertical_basis == "user_supplied" and not plan.vertical_note.strip():
        issue("vertical_source", "Record the source of the supplied vertical dimensions.")
    if not plan.geometry_confirmed:
        issue("coverage", "Confirm you checked the overlay, including missing walls and openings.")
    if not plan.reviewer.strip():
        issue("reviewer", "Enter a reviewer name for the local review record.")
    walls = model_walls(plan, scale) if scale else []
    by_id = {w["id"]: w for w in walls}
    lines = [LineString([w["a"], w["b"]]) for w in walls]
    dangling = []
    for i, (w, line) in enumerate(zip(walls, lines)):
        if line.length < .1:
            issue("short_wall", "Wall is shorter than 0.10 m; correct or remove it.", [w["id"]])
        for j in range(i):
            if line.intersection(lines[j]).length > .02:
                issue("overlap", "Wall axes overlap. Resolve the duplicate/overlapping segments.", [w["id"], walls[j]["id"]])
        from shapely.geometry import Point as ShapelyPoint
        if any(not any(j != i and other.distance(ShapelyPoint(p)) < .005 for j, other in enumerate(lines)) for p in (w["a"], w["b"])):
            dangling.append(w["id"])
    if dangling:
        issue("unjoined", f"{len(dangling)} wall(s) have an unjoined endpoint; inspect for missing segments.", dangling, "warning")
    intervals = {}
    for o in plan.openings:
        if o.status != "accepted":
            continue
        host = by_id.get(o.wall_id)
        if not host:
            issue("missing_host", "Opening must reference an accepted wall.", [o.id])
            continue
        length = distance(host["a"], host["b"])
        left, right = o.t*length - o.width_m/2, o.t*length + o.width_m/2
        if left < -1e-8 or right > length + 1e-8:
            issue("opening_bounds", "Opening extends past its host wall. It will not be clipped or moved.", [o.id, o.wall_id])
        if o.sill_m + o.height_m > host["height_m"] + 1e-8:
            issue("opening_height", "Opening exceeds the wall height.", [o.id, o.wall_id])
        if o.kind == "door" and o.sill_m != 0:
            issue("door_sill", "Door sill must be zero for this first-version exporter.", [o.id])
        for lo, hi, sid in intervals.get(o.wall_id, []):
            if min(right, hi) - max(left, lo) > 1e-6:
                issue("opening_overlap", "Openings overlap along their host wall.", [o.id, sid])
        intervals.setdefault(o.wall_id, []).append((left, right, o.id))
    from .vectorize import bridge_gaps
    bridges = bridge_gaps(walls, DOOR_GAP_M) if walls else []
    rooms = room_polygons(walls, bridges) if all(line.length > 0 for line in lines) else []
    if walls and not rooms:
        issue("no_rooms", "No closed room boundaries found. No room areas will be claimed.", severity="warning")
    if plan.vertical_basis == "assumed":
        issue("assumed_verticals", "Vertical dimensions are acknowledged assumptions, not recovered measurements.", severity="warning")
    issue("scope_limit", "Review checks consistency and records your decisions; it does not independently verify building accuracy or completeness.", severity="warning")
    return {"ready": not any(i["severity"] == "error" for i in issues), "issues": issues,
            "calibration": cal, "walls": walls, "rooms": rooms, "door_gaps": bridges,
            "counts": {"accepted": len(walls), "pending": len(pending), "openings": len(plan.openings), "rooms": len(rooms)}}
