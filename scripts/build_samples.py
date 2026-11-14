"""Rebuild the bundled sample drawings (data/samples) from the Junction dataset.

usage (repo root):
    PYTHONPATH=backend .venv/bin/python scripts/build_samples.py /path/to/junction-dataset-preview/data [out_dir]

Crops and sheet scales are hand-picked per sample; uploads use auto-crop and an estimated scale instead.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from ymir.build import build_drawing  # noqa: E402

SAMPLES = [
    {"id": "swiss", "title": "Grundriss 2. OG", "file": "swiss_dwellings/site1000_bldg2082_floor9385.png",
     "dataset": "Swiss Dwellings", "kind": "CAD export", "crop": (875, 60, 1345, 1215),
     "scale_denominator": 200, "scale_text": "1:200 (A3)", "read_text": True},
    {"id": "finna", "title": "Ändringsritning, 1921", "file": "finna/hka.139877703731200_160449953667200.jpg",
     "dataset": "Finna archive", "kind": "Scanned drawing", "read_text": False},
    {"id": "cubi", "title": "Apartment plan", "file": "cubicasa5k/high_quality-10785.png",
     "dataset": "CubiCasa5k", "kind": "Raster plan", "read_text": False},
]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    dataset = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / "samples"
    out.mkdir(parents=True, exist_ok=True)
    library = []
    for s in SAMPLES:
        built = build_drawing((dataset / s["file"]).read_bytes(), drawing_id=s["id"], title=s["title"], dataset=s["dataset"],
                              kind=s["kind"], file=s["file"], crop=s.get("crop"), scale_denominator=s.get("scale_denominator"),
                              scale_text=s.get("scale_text"), read_text=s["read_text"])
        (out / built.payload["image"]["url"]).write_bytes(built.image)
        (out / f"{s['id']}.json").write_text(json.dumps(built.payload, ensure_ascii=False))
        (out / f"{s['id']}.trace.json").write_text(json.dumps(built.trace, ensure_ascii=False, indent=1))
        library.append({"id": s["id"], "title": s["title"], "source": {"dataset": s["dataset"], "kind": s["kind"]}})
        p = built.payload
        print(f"{s['id']}: {len(p['walls'])} walls, {len(p['rooms'])} rooms, {len(p['texts'])} texts")
    (out / "library.json").write_text(json.dumps(library, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
