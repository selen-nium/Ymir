# Stoa Lens in Ymir (local)

Ymir keeps its branding, sheet placement, wall editor, and Build pipeline. Ask uses
`stoa_lens.service.run`; machine findings appear as evidence overlays. Human-verified
window labels also add schematic window openings to the existing model. The
historical mock QA modules remain only for regression tests and archived behavior.

Run the backend with `scripts/start-local-lens.sh`. In a second terminal run
`cd frontend && YMIR_API_BASE=http://127.0.0.1:8011 npm run dev -- --port 3011`.
Install Lens with `pip install -r requirements-lens.txt` in Ymir's environment.
This checkout uses a Python 3.13 environment inheriting the machine's existing
Torch runtime, with Lens installed editable. D-FINE and Raster2Seq run in their
existing isolated environments. Secrets are read only by Lens on the backend.

`var/lens/budget.sqlite` is the new, immutable US$25 ceiling for live integration
checks and initial local use. `/api/lens/status` reports readiness, settled spend,
held reservations, and remaining funds. Unknown charges keep reservations.
No retry path repeats paid work. Do not delete/reset this ledger to resume work.

Ask requires `request_id`, `drawing_id`, `selected_drawing_ids` (1–3 unique IDs,
including the active drawing), `project_id`, `conversation_id`, `question`, and
optional `prior_job_ids`/Build `context`. One conversation is currently the
existing browser thread for its drawing/building. The server supplies its six
latest completed turns, irrespective of which earlier IDs the browser remembers.
Old answers only interpret the question. Current source validation binds every
new answer to the selected source pages. Build scale and edited walls are recorded
as context, never used as Lens calibration.

`var/lens/jobs.sqlite` stores the persistent queue, idempotency keys, sequenced
NDJSON events, and immutable machine answers. A filesystem lock admits one
inference worker across API processes. Startup marks in-flight jobs interrupted;
queued work may start, but interrupted work never resumes automatically. Cancelling
sets a durable flag, stops after the current operation, and blocks further dispatch.
A disconnected HTTP subscriber does not cancel the job. Lookup and reconnect use
`GET /api/lens/jobs/{id}` and `/events?after=N`; cancellation uses `POST /cancel`.
`/artifacts/manifest`, `/artifacts/result`, and `/artifacts/events` expose only
sanitized, named artifacts. Provider journals, source paths, and credentials are
not arbitrary downloads. Full backend receipts stay on disk for local inspection.

`POST /api/documents` retains the exact original and its SHA256. Inventory offers
PDF page selection, IFC storey selection, and explicit SVG room IDs. Preparation
uses `POST /api/documents/{id}/sheets`. PDFs retain single-page native PDFs plus
original page numbers and full-document coverage. Canonical raster pixels match
Lens's PDF registration. Build's crop/resize is inverted into that image for walls,
rooms, holes, text and other boxes, while its transform is retained. Existing
bundled samples retain their historical image frame and exposed-sample provenance;
no original PDF witness is invented for them.

IFC/SVG extraction uses the existing native backend and retains its restrictions.
Native room-footprint previews are labeled and may provide only room evidence.
SVG areas require explicit endpoints, length, unit, reading and reviewer. Printed
areas cannot supply scale. Unsupported sources/geometry remain unavailable.

Counts and measurements are host results; model readings, printed areas, proposal
scores, and human corrections are distinct. `needs_review` is explicit and has no
invented confidence. Review groups are per sheet/class and link to the originating
job. The server derives model attribution and validates the sheet, class, proposal
IDs and bounds. It timestamps local-user corrections as separate review records;
the immutable Lens answer is preserved. Citations switch to their sheet, focus the
source region, and use existing placement for 3D. Unplaced sources remain in 2D.

Validation commands:

```
PYTHONPATH=backend .venv/bin/python -m pytest -q
npm --prefix frontend run check
npm --prefix frontend run build
/mnt/berstorage/stoa/.venv/bin/python -m pytest -q /mnt/berstorage/stoa/tests/test_lens_*.py
```

`tests/browser_backend.py` is an explicit no-inference browser fixture, separate
from the production launch script. `scripts/check_lens_browser.cjs` runs browser
QA against that fixture on :8012 and frontend :3011. Its local Playwright paths
reuse this machine's installed browser. `scripts/check_lens_live.py` uses the
real backend on :8011, the same $25 ledger, public AECV images and an original
AEC-Bench PDF; selection and events live under `var/live-checks/`. These checks
are exposed integration diagnostics, not a benchmark score or engineering review.
Reference labels are opened only by the separate offline scorer after completion.

## Local verification, 15 November 2026

The Ymir suite passes 58 tests; frontend ESLint, TypeScript, 20 unit tests and the
production build pass. The integration's Lens regression set and analytic native
geometry tests pass (100 tests). Browser QA covers three-sheet selection, a running-job reload
and event reconnect, review persistence, PDF page import and desktop/mobile renders.
A separate, concurrently added Kimi diagnostic suite in the shared Stoa workspace
currently has six failures because it expects a `routing_policy` injection argument
that the production adapters do not support. Its files and study routing are left
untouched. The integration retains the production D-FINE/Kimi routing policy.

The first live AECV count returned 11 against its reference count of 10, so the
count is not an accuracy success; the completed result still supports manual review.
The AEC-Bench PDF reading returned the reference 2.3 inches with positioned native
text. A live follow-up used the prior turn but conservatively returned `needs_review`
after invalid evidence IDs. That trajectory and its charges are retained. The
finalization inventory now explicitly enumerates valid view IDs and explains that
page IDs are source identities, not reading IDs; local regression covers that
clarification. No paid job was rerun to replace these outcomes.

The first two job manifests predate a prospective fix that preserves the imported
source's project identity independently of its workspace identity. They remain
unchanged; the follow-up records the correct `aecv-0000-0001` source identity.
`var/live-checks/offline-comparison.json` compares the two primary checks against
reference annotations without sending those labels to inference. The job, source,
provider and budget receipts remain under `var/` for review.

The final live ledger is US$0.161651 spent, US$0 held, and US$24.838349
remaining. `var/live-checks/verification.json` records the final check results.

## Trace execution order

Lens traces display each model response and tool call in execution order. Repeated
inspections get separate steps. The sequence scrolls horizontally at a fixed node
width and follows new steps until the viewer scrolls back. Both preview and live
nodes contain their labels; full descriptions and model identifiers remain
available on hover and keyboard focus.

Detection activity records the host-selected backend and model before dispatch,
plus the requested class. The trace displays D-FINE-L or Kimi on the detection
step, retaining that identity on failures. Earlier Lens traces are projected from
stored activity receipts when replayed; no archived events or paid work are rerun.
`check_lens_trace_browser.cjs` exercises the no-inference fixture and historical
trace replay. The optional Kimi diagnostic routing override remains separate.

## Ymir copy and editable labels

The interface calls the product Ymir. Historical run receipts keep their original
names and wording; the UI presents count answers as “I found 3 windows” and shows
short caveats with a link to the complete result. Package names and API paths
remain compatible with the local Lens integration.

A saved review can be reopened with **Edit labels**. The editor restores resized,
rejected and user-drawn boxes. Saving appends a server-attributed record linked by
`revision_of`; it does not overwrite the earlier labels or machine answer. A stale
revision returns 409 rather than replacing someone else's newer edits. Job lookup
and listing include the latest `human_reviews` per sheet/class, so labels restore
even without browser conversation storage. Human corrections replace that group's
displayed count and citations. Each accepted object has one human-verified label;
its source evidence remains clickable in 2D and 3D. Other sheets/classes and
non-count uncertainty keep their own evidence and review state.

`check_ymir_labels_browser.cjs` checks editing, server restoration, failed saves,
cancelling edits, a single citation per object, and desktop/mobile layout against
the no-inference fixture. These checks do not spend the shared inference budget.


## Windows from reviewed labels

The latest saved window review for each sheet supplies its model windows. Accepted
boxes align to nearby walls in the sheet frame; distant or unaligned boxes remain
2D evidence and the model reports that they need a wall. Window widths and positions
follow those boxes and the existing Build placement/scale. Sill and head heights
are schematic (32% and 78% of the wall height), explicitly labelled as estimated.
This does not calibrate Lens measurements or alter the archived source geometry.

The viewer extrudes real wall openings and adds frames and glazing. Label edits
preview immediately; cancellation restores saved windows, and a failed save keeps
the preview editable. A successful save settles the glazing with a brief animation
while retaining the camera view. Added, resized and removed windows use keyed,
interruptible transitions in the existing render loop. Reduced motion applies the
new state immediately. Saved windows stay in Build, on other placed storeys, and
after reload, independently of which answer is open.

The active Build pipeline no longer auto-dispatches its legacy text extractor or
emits the retired vision step. The UI filters these obsolete steps from frozen
sample traces without rewriting those files. The landing page reflects the current
Build path. `check_ymir_windows_browser.cjs` verifies the no-inference window flow.

## Large raster imports

Image intake accepts originals up to 120 megapixels under the existing byte upload
limit. Large PNG/JPEG/WebP scans get a working image bounded to 30 megapixels and
an 8,000-pixel longest side. The exact original bytes/hash remain available. The
sheet and job snapshot retain the original dimensions, EXIF orientation, and
forward/inverse transforms between original image coordinates and the displayed
working image. Build's independent resize/crop still maps into that displayed
frame. Existing PDFs retain Lens's exact native-text raster registration.

The former 30-megapixel rejection could reject a highly compressed 1–2 MB drawing.
The browser now imports the retained 9,934 × 7,017 failure case as a 6,516 × 4,603
working image, with the original preserved. Archived intake failures are unchanged.
`test_large_image_import.py` covers resizing and all eight EXIF orientations;
`check_ymir_large_image_browser.cjs` checks the actual browser import with no
inference dispatch.


## Featured sample catalog and GitHub source

Home lists CubiCasa apartment 2536, Swiss Dwellings building 1298/floor 5733,
AC20, Duplex, DigitalHub (IFC Models), and Xcel Sports Complex (Opening Design).
CubiCasa and Swiss reuse sources from completed Sol trajectories. Their saved
outputs are predictions, not reference labels or new verified measurements.
Native IFC models and raster wall previews are prepared once and served as cached
assets. Sports Complex includes five related plans/details/schedules; overlapping
reference views are attached rather than assigned fabricated 3D placements.

`featured-catalog.json` changes visibility prospectively. Legacy sample files,
IDs and the original project manifest still resolve saved conversations. Bundled
originals have hashes and file credits, and are available through the document
endpoints. The two default experiment datasets remain AECV-Bench and AEC-Bench;
this showcase selection follows the user's explicit dataset request.

`vendor/stoa-runtime` contains the Ask code snapshot and required VISTA modules,
with implementation hashes and the upstream VISTA notice. It excludes credentials,
reference annotations, model weights, historical ledgers and provider responses.
`requirements.txt` installs that source as an editable dependency. The local
launcher uses `YMIR_STOA_ROOT` (a sibling Stoa asset checkout by default) and reuses
the existing detector/geometry environments. The Modal model smoke checks and Vercel/Modal configuration are documented in
[hosting.md](hosting.md). The API cutover preserves this same ledger and source
state; the local workspace remains available until that cutover.

## Browser checks

The browser scripts use this checkout's pinned Playwright dependency. Install the
matching browser with `cd frontend && npx playwright install chromium`. Run Next on
port 3011. Start the explicit no-inference fixture on port 8012 with
`PYTHONPATH=backend:tests:vendor/stoa-runtime/src YMIR_DATA_DIR=/tmp/ymir-browser-qa .venv/bin/python -m uvicorn browser_backend:app --port 8012`.
Then run `node scripts/check_ymir_windows_browser.cjs`,
`node scripts/check_ymir_labels_browser.cjs`,
`node scripts/check_lens_trace_browser.cjs`, and
`node scripts/check_featured_samples_browser.cjs`. These tests route API traffic to
the fixture; the production launcher never imports it.

`check_direct_api_browser.cjs` instead uses a production frontend on port 3013
built with `NEXT_PUBLIC_YMIR_API_BASE=http://127.0.0.1:8013` and a separate fixture
on port 8013. Its API traffic is real cross-origin HTTP, with no interception.
