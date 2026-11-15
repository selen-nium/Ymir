"""Rebuild the walls of prebuilt image samples with the current wall finder (no WallNet).

    PYTHONPATH=backend python scripts/rebuild_sample_walls.py            # every sample built by WallNet
    PYTHONPATH=backend python scripts/rebuild_sample_walls.py cubi swiss # just these

Each sample keeps its image and pixel frame, so placements, rooms, text, scale and saved citations stay
aligned. Only `walls`, `openings` (doors and windows), the source's wall-input note and the trace's
"Find plan" / "Detect walls" steps change. Rooms are kept: on several samples they come from an earlier room analysis, not from walls.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

from ymir import settings
from ymir.build import build_drawing
from ymir.build.floorplan.extract import normalize
from ymir.drawings import DrawingStore


def wallnet_samples() -> list[str]:
    out = []
    for trace in sorted(settings.SAMPLES_DIR.glob("*.trace.json")):
        events = json.loads(trace.read_text())
        if any(e.get("type") == "node" and e.get("id") == "walls" and "wallnet" in (e.get("tool") or "").lower() for e in events):
            out.append(trace.name.removesuffix(".trace.json"))
    return out


def rebuild(sid: str, store: DrawingStore) -> dict:
    path, trace_path = settings.SAMPLES_DIR / f"{sid}.json", settings.SAMPLES_DIR / f"{sid}.trace.json"
    payload = json.loads(path.read_text())
    image = store.image_path(sid)
    raw = image.read_bytes()
    _, normalized = normalize(raw)
    src = payload.get("source", {})
    built = build_drawing(raw, drawing_id=sid, title=payload["title"], dataset=src.get("dataset") or "Sample",
                          kind=src.get("kind") or "Prepared drawing", file=src.get("file") or image.name)
    x0, y0 = built.payload["source"]["crop"][:2]
    width, height = Image.open(image).size
    fx, fy = width / normalized["width"], height / normalized["height"]
    walls = []
    for w in built.payload["walls"]:
        walls.append({"id": w["id"],
                      "a": [round((w["a"][0] + x0) * fx, 1), round((w["a"][1] + y0) * fy, 1)],
                      "b": [round((w["b"][0] + x0) * fx, 1), round((w["b"][1] + y0) * fy, 1)],
                      **({"thickness_px": round(w["thickness_px"] * (fx + fy) / 2, 1)} if w.get("thickness_px") else {})})
    openings = [{**o, "box": [round((o["box"][0] + x0) * fx, 1), round((o["box"][1] + y0) * fy, 1),
                              round((o["box"][2] + x0) * fx, 1), round((o["box"][3] + y0) * fy, 1)]}
                for o in built.payload.get("openings", [])]
    before = len(payload.get("walls", []))
    payload["walls"] = walls
    payload["openings"] = openings
    payload["wall_blocks"] = [{**b, "polygon": [[round((px + x0) * fx, 1), round((py + y0) * fy, 1)] for px, py in b["polygon"]]}
                              for b in built.payload.get("wall_blocks", [])]
    payload.setdefault("source", {})["wall_input"] = built.payload["source"].get("wall_input")
    payload["source"]["walls_built_by"] = "Stoa Lens converter (research/stoa-lens) after text removal; WallNet retired"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    # Swap the plan and wall steps of the stored trace for the new build's; keep every other step.
    new = {(e["type"], e["id"]): e for e in built.trace if e.get("id") in ("plan", "walls")}
    events = json.loads(trace_path.read_text()) if trace_path.exists() else []
    events = [new.get((e.get("type"), e.get("id")), e) for e in events]
    trace_path.write_text(json.dumps(events, ensure_ascii=False))
    return {"id": sid, "walls_before": before, "walls_after": len(walls),
            "doors": sum(o["kind"] == "door" for o in openings), "windows": sum(o["kind"] == "window" for o in openings)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ids", nargs="*", help="sample ids (default: every sample whose walls came from WallNet)")
    ids = parser.parse_args().ids or wallnet_samples()
    if not ids:
        sys.exit("No WallNet-built samples left.")
    store = DrawingStore()
    for sid in ids:
        print(json.dumps(rebuild(sid, store)), flush=True)
