# Ymir

Ask questions about a construction drawing and get answers that point to where they came from, in the
drawing and in a 3D model built from it. When the model isn't sure, it asks you to check its boxes, and
your corrections are kept as training labels.

Built for the Stoa challenge at Junction 2026.

## Ask, review and sources

This checkout connects Ask to Stoa Lens with a durable single-worker queue,
explicit scope of up to three sheets, follow-ups, source-linked evidence and
human review. PDF/image originals and native IFC/SVG geometry retain provenance.
Local runs and deployment checks share the same durable US$25 ledger, with no automatic paid retries.
See [local setup and integration contracts](docs/stoa-lens-local.md), the
[compact counter](docs/compact-counter.md) and [current validation](docs/lens-validation-20261115.md).

## Run it locally

Needs Python 3.12 and Node 20+. Two processes: the API on :8000 and the Next.js frontend on :3000, which
forwards `/api/*` to it. (Git LFS is only needed for the retired WallNet weights in `weights/`.)

```sh
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
#   or: python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=backend .venv/bin/uvicorn ymir.app:app --reload --port 8000

cd frontend && npm install && npm run dev          # second terminal
```

Open http://localhost:3000 for the landing page, **Open the app** for home (uploads, buildings and their
conversations), or go straight to the sample building at http://localhost:3000/building?id=ac20: place the
unplaced **A-101 Level 1 plan** (its **Place** button in the sheet list, then two matching corners), and ask
*Where does the main drain leave the building?* to see an answer point at the missing plumbing plan.
Single drawings open at `/drawing?id=cubi`. Ask *How many windows are there?* to
inspect the source-linked result or review uncertain proposals. Proposal scores
are uncalibrated; Ask does not invent an answer-confidence percentage.

## How it fits together

```
frontend/  (Next.js · Vercel)                        backend/ymir/  (FastAPI · Modal: deploy/modal_app.py)
  app/  /  /home  /building  /drawing  ──/api/*──▶     app.py        the API
  components/workspace/  rail, views, Build, Ask        lens_jobs.py  durable queue → Ask runtime → linked evidence
  components/landing/  3D hero, architecture            documents.py  originals, pages and native geometry
  lib/workspace.ts  the page's state and actions        build/        image → walls, rooms and estimated scale
  lib/session.ts  active sheet, box review              drawings.py   sample + uploaded drawings, projects
  lib/project.ts  sheets, placement, ghosts             reviews.py    corrected boxes → SQLite
  lib/plan2d.ts · viewer3d.ts · trace.ts  2D, 3D, trace
  lib/persist.ts  threads, your buildings (browser)
```

The 2D sheet, the three.js model and the trace are imperative renderers in `lib/`; React components mount them
and render everything else from the workspace store (zustand).

- **Build:** `build/pipeline.py` turns a plan image into a drawing payload: walls, doors and windows,
  estimated scale and rooms. Walls come from the Stoa Lens PNG-to-IFC converter in `research/stoa-lens`
  (`floorplan/converter.py`), run on a cleaned copy (`floorplan/clean.py`): glyph-sized text is removed, the
  crop targets a dominant plan while keeping multi-plan sheets whole, and line drawings (CubiCasa scans, CAD sheets) are
  darkened so double-line walls read as solid. Each wall's two faces are paired into one centreline at any
  angle, and walls run through the doors and windows the converter finds. The shown image keeps its text.
  WallNet is retired. Samples are prebuilt in `data/samples/` (`scripts/rebuild_sample_walls.py` rebuilds
  their walls); uploads run it live.
- **Ask:** `POST /api/ask` snapshots one to three selected sheets and records a durable job.
  The source-controlled runtime in `vendor/stoa-runtime` calls DeepSeek and Kimi on Verda,
  D-FINE for its trained classes, and native or Raster2Seq room tools. Counts use recorded
  proposals. Measurements require source calibration.
- **Review:** incomplete counts with proposals open the box editor. Completed counts can be
  reviewed manually. Saved labels remain editable; each edit adds a server-attributed
  revision. Verified windows animate into the walls. Their height remains an estimate.
- **Evidence:** citations open the correct sheet and region. Placed sheets also project evidence
  into the building. Original model answers remain intact beside human corrections; uncertain
  answers carry `needs_review` rather than an invented confidence percentage.

## Projects: several sheets, one building

A project is a building made of sheets: floor, foundation, framing, roof and plumbing plans lie flat at their level,
elevations stand on their side, schedules and details are linked but not placed. Parts no sheet covers are
**ghosts** (dashed, see-through), guessed from the base plan's outline; clicking one asks for that sheet. A single
drawing opens as a one-sheet project, so unseen drawings work as before. Design:
`docs/superpowers/specs/2026-11-14-multi-sheet-project-design.md`.

- Active sample visibility lives in `data/samples/featured-catalog.json`; legacy projects remain in `projects.json`. `scripts/build_ifc_project.py` renders one from an IFC
  model in the dataset (needs `ifcopenshell numpy pillow shapely`, outside the app's requirements).
- Each sheet's view has a `kind` (`floor_plan`, `foundation_plan`, `plumbing_plan`, `elevation`, `schedule`, ...,
  see `frontend/lib/project.ts`) and a `placement` (`plan` with a px → metres `transform`, `elevation` with a
  base line, or `attached`). The viewer's own changes (added sheets, kinds, placements, levels), the buildings
  they start (**New building** on home, or **Start a building from this** on a drawing) and every conversation are
  kept in the browser (`frontend/lib/persist.ts`). Each sheet in the list has **Place** and **Remove**.
- For the models: a drawing payload may add `kind`; an answer's citations may carry `sheet` (a drawing id); an
  abstained answer may carry `needs: {kind, level}` so the frontend lights up the missing sheet's ghost (until it
  does, the frontend guesses from the question). `/api/ask` receives `project_id`,
  `conversation_id`, `drawing_id` and `selected_drawing_ids`.

## Model runtime

D-FINE-L uses the pinned fourteen-head Junction EMA checkpoint. Other object classes
route to Kimi. Raster2Seq supplies room proposals; supported IFC/SVG sources use native
geometry. Model identities, checkpoint hashes and extraction limits accompany each job.
See [hosting](docs/hosting.md) for the separate GPU environments and verified deployment.

The former `ymir.models` adapters and calibration helpers remain for historical records
and tests. `YMIR_MODELS` does not replace the current Ask runtime.

## API

| Method | Path | |
| --- | --- | --- |
| GET | `/api/library` | Sample and uploaded drawings |
| GET | `/api/projects` · `/api/projects/{id}` | Sample projects (buildings made of several sheets) |
| GET | `/api/drawings/{id}` · `/trace` · `/image` · `/suggestions` | Payload, build trace, image, example questions |
| POST | `/api/drawings` | Upload a plan image (PNG/JPEG/WebP) and build it |
| DELETE | `/api/drawings/{id}` | Delete an uploaded drawing (samples: 403); its reviews stay as labels |
| GET | `/api/agents` | The agent graph shown before a question |
| POST | `/api/ask` | Idempotent request → durable job and NDJSON progress/answer events |
| GET | `/api/lens/status` | Readiness, remaining funds and held reservations |
| GET | `/api/lens/jobs/{id}` · `/events` · `/artifacts` | Saved result, reconnectable events and allowlisted artifacts |
| POST | `/api/lens/jobs/{id}/cancel` | Stop further dispatch after the current operation |
| POST | `/api/documents` · `/api/documents/{id}/sheets` | Inventory an original, then prepare selected pages or native geometry |
| POST / GET | `/api/reviews` | Store / list corrected boxes (`?drawing_id=`) |
| GET | `/api/health` | Status and loaded models |

Ask requests include `request_id`, `drawing_id`, `selected_drawing_ids`, `project_id`,
`conversation_id`, `prior_job_ids` and `question`. `context` retains Build edits; it does
not calibrate Ask measurements. An initial job event provides the durable ID; sequenced
progress and answer events can be replayed after reload. Reusing a request ID never
starts a second inference job.

Reviews retain the originating job, sheet and class. The server attributes each record;
editing a saved review creates a revision and preserves the earlier record.

## Settings

| Variable | Default | |
| --- | --- | --- |
| `YMIR_STOA_ROOT` | sibling Stoa checkout via local launcher | Existing model assets, provider metadata and backend credentials |
| `YMIR_GPU_BACKEND` | local | Set `modal` for private GPU functions |
| `YMIR_MODEL_READINESS` | none | Verified Modal smoke receipt for the hosted API |
| `YMIR_MODELS` | `ymir.models.mock:MockDetector` | Legacy adapters; current Ask uses the runtime above |
| `YMIR_DATA_DIR` | `var/` | Uploads and the review database (use a persistent disk when deployed) |
| `YMIR_DB` | `$YMIR_DATA_DIR/ymir.db` | SQLite file for reviews |
| `YMIR_WEIGHTS_DIR` | `weights/` | Model weights |
| `YMIR_TRACE_PACE` | `0.25` | Seconds between trace steps so the diagram is readable; `0` for none |
| `YMIR_MAX_UPLOAD_MB` | `15` | Upload limit |
| `YMIR_AUDIT_RATE` | `0.05` | Legacy review calibration only |
| `YMIR_CALIBRATION_PRIOR` | `10` | Legacy review calibration only |
| `YMIR_CORS_ORIGINS` / `YMIR_CORS_ORIGIN_REGEX` | none | Origins allowed to call the API directly (only needed with `NEXT_PUBLIC_YMIR_API_BASE`) |
| `YMIR_API_BASE` (frontend) | `http://localhost:8000` | Local Next.js `/api/*` proxy target |
| `NEXT_PUBLIC_YMIR_API_BASE` (frontend) | none | Browser API origin; required HTTPS Modal origin on Vercel |

## Deploy (frontend on Vercel, API on Modal)

The frontend calls the Modal API directly for uploads and durable job streams.
D-FINE-L and Raster2Seq run in separate private GPU functions; DeepSeek and Kimi
remain on Verda. The CPU API keeps one writer for jobs, reviews and the shared
US$25 ledger. GPU workers scale to zero.

Follow [the hosting setup](docs/hosting.md) for model smoke checks, state cutover,
backend secrets and deployment. Import `frontend` as the Vercel Root Directory and
set `NEXT_PUBLIC_YMIR_API_BASE` to the HTTPS Modal API origin before building.
Set the matching frontend origin in Modal's `YMIR_CORS_ORIGINS`.

## Tests

```sh
.venv/bin/python -m pytest -q              # API, routes, reviews, uploads, model registry
cd frontend && npm run check               # ESLint, TypeScript, Vitest (placement, ghosts, review, browser store)
cd frontend && npm run build               # production build
```

CI (`.github/workflows/ci.yml`) runs both on every push and pull request.

## Data

`data/samples/featured-catalog.json` selects the prepared examples shown on Home:
CubiCasa apartment 2536 and a Swiss Dwellings floor previously inspected by Sol;
AC20 and Duplex from Junction IFC-Bench; DigitalHub from IFC Models; and five
Sports Complex sheets from Opening Design. Geometry and native text are cached,
so opening a sample never starts a paid run. Prior model selections are labelled
predictions and remain separate from reference annotations.

The original Swiss, CubiCasa, Finna and AC20 sample IDs remain available for saved
conversations, but the old examples are omitted from Home. Original source bytes,
hashes, project identity, exposure notes and file credits accompany the new cache.
Recreate it explicitly with `scripts/prepare_featured_samples.py /path/to/stoa`.
See `docs/stoa-lens-local.md` and `vendor/stoa-runtime/README.md` for Ask setup.
