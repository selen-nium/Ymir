# Ymir

Ask questions about a construction drawing and get answers that point to where they came from, in the
drawing and in a 3D model built from it. When the model isn't sure, it asks you to check its boxes, and
your corrections are kept as training labels.

Built for the Stoa challenge at Junction 2026.

## Run it locally

Needs Python 3.12, Node 20+ and [Git LFS](https://git-lfs.com) (for the wall-model weights). Two processes:
the API on :8000 and the Next.js frontend on :3000, which forwards `/api/*` to it.

```sh
git lfs install && git lfs pull
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
#   or: python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=backend .venv/bin/uvicorn ymir.app:app --reload --port 8000

cd frontend && npm install && npm run dev          # second terminal
```

Open http://localhost:3000 for the landing page, **Open the app** for home (uploads, buildings and their
conversations), or go straight to the sample building at http://localhost:3000/building?id=ac20: place the
unplaced **A-101 Level 1 plan** (its **Place** button in the sheet list, then two matching corners), and ask
*Where does the main drain leave the building?* to see an answer point at the missing plumbing plan.
Single drawings open at `/drawing?id=cubi`: on the CubiCasa sample, *How many windows are there?* comes back at
58% confidence and opens the box review.

## How it fits together

```
frontend/  (Next.js · Vercel)                        backend/ymir/  (FastAPI · Modal: deploy/modal_app.py)
  app/  /  /home  /building  /drawing  ──/api/*──▶     app.py        the API
  components/workspace/  rail, views, Build, Ask        qa/           planner → route → tools → verifier, streamed
  components/landing/  3D hero, architecture            models/       detectors (plug yours in here)
  lib/workspace.ts  the page's state and actions        build/        image → walls, rooms, text, scale (uploads)
  lib/session.ts  active sheet, box review              drawings.py   sample + uploaded drawings, projects
  lib/project.ts  sheets, placement, ghosts             reviews.py    corrected boxes → SQLite
  lib/plan2d.ts · viewer3d.ts · trace.ts  2D, 3D, trace
  lib/persist.ts  threads, your buildings (browser)
```

The 2D sheet, the three.js model and the trace are imperative renderers in `lib/`; React components mount them
and render everything else from the workspace store (zustand).

- **Build:** `build/pipeline.py` turns a plan image into a drawing payload: WallNet walls (or OpenCV lines
  without weights), RapidOCR labels, scale, rooms. Samples are prebuilt in `data/samples/`; uploads run it live.
- **Ask:** `POST /api/ask` picks a route in `qa/routes.py` (symbol count, room area, room-type count, largest
  room, room count, wall length, or abstain) and streams trace events while its tools run.
- **Review:** a box-count answer under 0.8 confidence makes the frontend ask the user to check the model's
  boxes (count/drop, resize, draw). The corrected record goes to `POST /api/reviews`. A small share of
  confident answers (`YMIR_AUDIT_RATE`) is sent for a spot check too, so accuracy is also measured where the
  model is sure.
- **Confidence from reviews:** `calibration.py` turns reviews into the confidence of later answers. Each
  review says whether the detector's counted boxes were exactly the ones the person kept, and a count's
  confidence moves from the detector's own value toward how often people agreed with that detector on that
  class. A drawing someone already checked is answered from their boxes.

## Projects: several sheets, one building

A project is a building made of sheets: floor, foundation, framing, roof and plumbing plans lie flat at their level,
elevations stand on their side, schedules and details are linked but not placed. Parts no sheet covers are
**ghosts** (dashed, see-through), guessed from the base plan's outline; clicking one asks for that sheet. A single
drawing opens as a one-sheet project, so unseen drawings work as before. Design:
`docs/superpowers/specs/2026-11-14-multi-sheet-project-design.md`.

- Sample projects live in `data/samples/projects.json`. `scripts/build_ifc_project.py` renders one from an IFC
  model in the dataset (needs `ifcopenshell numpy pillow shapely`, outside the app's requirements).
- Each sheet's view has a `kind` (`floor_plan`, `foundation_plan`, `plumbing_plan`, `elevation`, `schedule`, ...,
  see `frontend/lib/project.ts`) and a `placement` (`plan` with a px → metres `transform`, `elevation` with a
  base line, or `attached`). The viewer's own changes (added sheets, kinds, placements, levels), the buildings
  they start (**New building** on home, or **Start a building from this** on a drawing) and every conversation are
  kept in the browser (`frontend/lib/persist.ts`). Each sheet in the list has **Place** and **Remove**.
- For the models: a drawing payload may add `kind`; an answer's citations may carry `sheet` (a drawing id); an
  abstained answer may carry `needs: {kind, level}` so the frontend lights up the missing sheet's ghost (until it
  does, the frontend guesses from the question). `/api/ask` also receives `project_id` and `sheet_id`.

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
    version = "v1"                                     # bump when retrained: reviews calibrate one version
    trained_on_reviews = False                         # True once trained on /api/reviews (see below)
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

The answer's confidence is then adjusted by people's reviews of your detector: `(agreed + 10 × yours) /
(reviews + 10)`, counting only reviews of the same `name` and `version` and class. When you train on the
reviews, use only records with `"split": "train"` and set `trained_on_reviews = True`: confidence is then
measured on the `holdout` drawings (one in five) you never saw.

To add a new kind of question, write a `Route` in `backend/ymir/qa/routes.py` and list it in `ROUTES`.

## API

| Method | Path | |
| --- | --- | --- |
| GET | `/api/library` | Sample and uploaded drawings |
| GET | `/api/projects` · `/api/projects/{id}` | Sample projects (buildings made of several sheets) |
| GET | `/api/drawings/{id}` · `/trace` · `/image` · `/suggestions` | Payload, build trace, image, example questions |
| POST | `/api/drawings` | Upload a plan image (PNG/JPEG/WebP) and build it |
| DELETE | `/api/drawings/{id}` | Delete an uploaded drawing (samples: 403); its reviews stay as labels |
| GET | `/api/agents` | The agent graph shown before a question |
| POST | `/api/ask` | `{drawing_id, question, context}` → NDJSON stream of `node` / `status` / `answer` events |
| POST / GET | `/api/reviews` | Store / list corrected boxes (`?drawing_id=`) |
| GET | `/api/health` | Status and loaded models |

`context` carries the user's Build decisions (`m_per_px`, `scale_confirmed`, `removed_walls`, `added_walls`),
so answers use their scale and walls. A count answer adds `confidence`, `confidence_basis: {model, reviews,
agreed}` and `review: {class, threshold, boxes: [{id, box, score}], audit?}` to the usual
`value / unit / text / citations / highlight`; `audit: true` asks for a spot check of a confident answer. A
count a person already checked comes back with `confidence: 1` and `source_review` instead of `review`.

Reviews are `drawing-qa-review-v1` records whose `objects` use the stoahackjunction annotation workbench fields
(`id, class, bbox_xyxy, subtype, status, proposal_id`), so they can join its training data. `model` also holds
the model's original `boxes` and `audit`; the server adds `model.detector` (which detector was checked) and
`split` (`train` or `holdout`, fixed per drawing).

## Settings

| Variable | Default | |
| --- | --- | --- |
| `YMIR_MODELS` | `ymir.models.mock:MockDetector` | Detectors to load, in priority order |
| `YMIR_DATA_DIR` | `var/` | Uploads and the review database (use a persistent disk when deployed) |
| `YMIR_DB` | `$YMIR_DATA_DIR/ymir.db` | SQLite file for reviews |
| `YMIR_WEIGHTS_DIR` | `weights/` | Model weights |
| `YMIR_TRACE_PACE` | `0.25` | Seconds between trace steps so the diagram is readable; `0` for none |
| `YMIR_MAX_UPLOAD_MB` | `15` | Upload limit |
| `YMIR_AUDIT_RATE` | `0.05` | Share of confident count answers sent for a spot check |
| `YMIR_CALIBRATION_PRIOR` | `10` | How many reviews the detector's own confidence is worth |
| `YMIR_CORS_ORIGINS` / `YMIR_CORS_ORIGIN_REGEX` | none | Origins allowed to call the API directly (only needed with `NEXT_PUBLIC_YMIR_API_BASE`) |
| `YMIR_API_BASE` (frontend) | `http://localhost:8000` | Where Next.js forwards `/api/*`; required on Vercel (the Modal URL) |
| `NEXT_PUBLIC_YMIR_API_BASE` (frontend) | none | Have the browser call the API directly instead of through the forward |

## Deploy (frontend on Vercel, API on Modal)

The browser only talks to the Vercel site; Next.js forwards `/api/*` to the API on Modal, so no CORS setup is
needed. API first (to get its URL), then the frontend.

**1. API on Modal**

```sh
pip install modal && modal setup            # once: log in
git lfs pull                                # real weights (the deploy refuses LFS pointers)
modal deploy deploy/modal_app.py
```

It prints `https://<workspace>--ymir-web.modal.run`; check `/api/health`. Or run **Actions → Deploy backend**
after adding the `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` secrets. `deploy/modal_app.py` sets the container size
(2 CPU, 4 GB), scale-to-zero after 5 minutes idle, and the `ymir-data` volume for uploads and `ymir.db`. GPU
models: add `gpu="T4"` there and install CUDA torch.

**2. Frontend on Vercel**

Import the repo in Vercel → **Root Directory `frontend`** (Next.js is detected) → add the environment variable
`YMIR_API_BASE` = the Modal URL → Deploy. The build fails without it. Every page is static, so Vercel serves it
from its CDN; pushes to `main` redeploy, pull requests get preview URLs. CLI instead:
`cd frontend && vercel link && vercel env add YMIR_API_BASE production && vercel deploy --prod`.

If large uploads or streamed answers ever misbehave through the forward, set `NEXT_PUBLIC_YMIR_API_BASE` to the
Modal URL instead (the browser then calls the API directly) and redeploy the API with
`YMIR_CORS_ORIGINS=https://<your-domain>` and `YMIR_CORS_ORIGIN_REGEX='https://<project>-[a-z0-9-]+\.vercel\.app'`.

**Day to day:** `modal app logs ymir` for logs; `modal volume get ymir-data ymir.db` to pull the reviews for
retraining. The first request after idle waits for a cold start (torch loads); set `min_containers=1` in
`deploy/modal_app.py` for a demo, then back to 0 to save credits. The API has no login: anyone with the
URL can ask and upload.

## Tests

```sh
.venv/bin/python -m pytest -q              # API, routes, reviews, uploads, model registry
cd frontend && npm run check               # ESLint, TypeScript, Vitest (placement, ghosts, review, browser store)
cd frontend && npm run build               # production build
```

CI (`.github/workflows/ci.yml`) runs both on every push and pull request.

## Data

`data/samples/` holds three prebuilt drawings from the Junction dataset: Swiss Dwellings (CC BY 4.0),
CubiCasa5K (CC BY-NC-SA 4.0, non-commercial) and a 1921 drawing from the Finna archive. Rebuild them with
`scripts/build_samples.py /path/to/junction-dataset-preview/data`. The CubiCasa window and door boxes in
`models/mock.py` are hand-placed stand-ins until the trained detectors are plugged in.
