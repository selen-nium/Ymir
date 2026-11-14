# Ymir

Ask questions about a construction drawing and get answers that point to where they came from, in the
drawing and in a 3D model built from it. When the model isn't sure, it asks you to check its boxes, and
your corrections are kept as training labels.

Built for the Stoa challenge at Junction 2026.

## Run it locally

Needs Python 3.12 and [Git LFS](https://git-lfs.com) (for the wall-model weights).

```sh
git lfs install && git lfs pull
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
#   or: python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=backend .venv/bin/uvicorn ymir.app:app --reload --port 8000
```

Open http://localhost:8000. Pick a drawing (Change), confirm the scale and walls in **Build**, then ask in **Ask**.
On the CubiCasa sample, *How many windows are there?* comes back at 58% confidence and opens the box review.

## How it fits together

```
frontend/  (static, no build step)            backend/ymir/
  index.html  Build + Ask UI       ──HTTP──▶    app.py        API + serves the frontend
  shared/api.js  backend client                 qa/           planner → route → tools → verifier, streamed
  shared/session.js  state, box review          models/       detectors (plug yours in here)
  shared/plan2d.js / viewer3d.js  2D + 3D       build/        image → walls, rooms, text, scale (uploads)
  shared/trace.js  agent diagram                drawings.py   sample + uploaded drawings
                                                reviews.py    corrected boxes → SQLite
```

- **Build:** `build/pipeline.py` turns a plan image into a drawing payload: WallNet walls (or OpenCV lines
  without weights), RapidOCR labels, scale, rooms. Samples are prebuilt in `data/samples/`; uploads run it live.
- **Ask:** `POST /api/ask` picks a route in `qa/routes.py` (symbol count, room area, room-type count, largest
  room, room count, wall length, or abstain) and streams trace events while its tools run.
- **Review:** a box-count answer under 0.8 confidence makes the frontend ask the user to check the model's
  boxes (count/drop, resize, draw). The corrected record goes to `POST /api/reviews`.

## Add your model

Detectors answer counting questions (doors, windows, toilets, sinks, ...). Anything that implements
`ymir.models.base.Detector` can be plugged in:

```python
# backend/ymir/models/yolo.py   (sketch: adapt to your model's output and class names)
from pathlib import Path
from ymir import settings
from ymir.models.base import Detection, DetectorResult

class YoloDetector:
    name = "YOLO11s"                                   # shown on the trace's Detector node
    classes = {"door", "window", "toilet_bowl", "sink", "bathtub", "shower"}
    THRESHOLDS = {"door": 0.40, "window": 0.42}        # per-class count thresholds picked on validation

    def __init__(self):
        from ultralytics import YOLO
        self.model = YOLO(settings.WEIGHTS_DIR / "counting-yolo11s.pt")

    def supports(self, drawing: dict, cls: str) -> bool:
        return drawing["source"].get("dataset") in {"CubiCasa5k", "Upload"}   # where it was trained / validated

    def detect(self, image_path: Path, drawing: dict, classes: list[str]) -> DetectorResult:
        r = self.model.predict(str(image_path), conf=0.1, verbose=False)[0]
        dets = []
        for i, (box, c, s) in enumerate(zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())):
            cls = r.names[int(c)]
            if cls in classes:
                dets.append(Detection(f"{cls[0]}{i + 1}", cls, tuple(box), float(s)))
        return DetectorResult(dets, {c: self.THRESHOLDS.get(c, 0.5) for c in classes})
        # pass confidence={cls: ...} once it is calibrated; until then a margin-based estimate is used
```

1. Put the weights in `weights/` (`*.pt`, `*.onnx`, `*.safetensors` go through Git LFS) and add your
   package to `requirements.txt`.
2. Register it, first one wins per class:
   `YMIR_MODELS="ymir.models.yolo:YoloDetector,ymir.models.mock:MockDetector"`.
3. Add a test next to `tests/test_models.py`.

Boxes are in the drawing image's pixels (`drawing["image"]["size"]`; samples are cropped to the plan).
Class names follow the stoahackjunction annotation schema. **Confidence** is the detector's confidence in the
*count* (0–1); under 0.8 the user is asked to check the boxes, so a calibrated value matters.

To add a new kind of question, write a `Route` in `backend/ymir/qa/routes.py` and list it in `ROUTES`.

## API

| Method | Path | |
| --- | --- | --- |
| GET | `/api/library` | Sample and uploaded drawings |
| GET | `/api/drawings/{id}` · `/trace` · `/image` · `/suggestions` | Payload, build trace, image, example questions |
| POST | `/api/drawings` | Upload a plan image (PNG/JPEG/WebP) and build it |
| GET | `/api/agents` | The agent graph shown before a question |
| POST | `/api/ask` | `{drawing_id, question, context}` → NDJSON stream of `node` / `status` / `answer` events |
| POST / GET | `/api/reviews` | Store / list corrected boxes (`?drawing_id=`) |
| GET | `/api/health` | Status and loaded models |

`context` carries the user's Build decisions (`m_per_px`, `scale_confirmed`, `removed_walls`, `added_walls`),
so answers use their scale and walls. A count answer adds `confidence` and
`review: {class, threshold, boxes: [{id, box, score}]}` to the usual `value / unit / text / citations / highlight`.

Reviews are `drawing-qa-review-v1` records whose `objects` use the stoahackjunction annotation workbench fields
(`id, class, bbox_xyxy, subtype, status, proposal_id`), so they can join its training data.

## Settings

| Variable | Default | |
| --- | --- | --- |
| `YMIR_MODELS` | `ymir.models.mock:MockDetector` | Detectors to load, in priority order |
| `YMIR_DATA_DIR` | `var/` | Uploads and the review database (use a persistent disk when deployed) |
| `YMIR_DB` | `$YMIR_DATA_DIR/ymir.db` | SQLite file for reviews |
| `YMIR_WEIGHTS_DIR` | `weights/` | Model weights |
| `YMIR_TRACE_PACE` | `0.25` | Seconds between trace steps so the diagram is readable; `0` for none |
| `YMIR_MAX_UPLOAD_MB` | `15` | Upload limit |

## Tests

```sh
.venv/bin/python -m pytest -q              # API, routes, reviews, uploads, model registry
node --test frontend/tests/*.test.mjs      # box review logic
```

## Data

`data/samples/` holds three prebuilt drawings from the Junction dataset: Swiss Dwellings (CC BY 4.0),
CubiCasa5K (CC BY-NC-SA 4.0, non-commercial) and a 1921 drawing from the Finna archive. Rebuild them with
`scripts/build_samples.py /path/to/junction-dataset-preview/data`. The CubiCasa window and door boxes in
`models/mock.py` are hand-placed stand-ins until the trained detectors are plugged in.
