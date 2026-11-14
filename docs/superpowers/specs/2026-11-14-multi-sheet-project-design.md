# Multi-sheet projects: every sheet gets a place in the building

Date: 2026-11-14 · Status: approved design, phase 1 built

## Problem

The frontend handles one kind of drawing: a floor plan. The payload is one drawing (walls, rooms, openings,
symbols), the 3D view raises walls on top of that image, Build's steps are fixed (scale → walls → model) and
the session holds one drawing. Foundation plans, slab and framing plans, plumbing plans, elevations, sections,
schedules and details have nowhere to go.

Judges will bring unseen drawings, so a single drawing of any kind must still work well. A building assembled
from several sheets is the extra on top.

## What the Stoa dataset contains

From the preview index and its README (`~/Downloads/junction-dataset-preview`):

- `ifc_models/`: whole buildings with plan sheets rendered from the model, one per level
  (`<model>_<level>.pdf`). Sheets from one model line up exactly. Their questions span levels
  ("total net volume of all slabs on '5. Sal'").
- `ifc_bench/`: IFC models asked about directly (`ac20_arc.ifc` is the KIT FZK-Haus, `duplex_arc.ifc` the NIBS duplex).
- `opening_design/`: real US drawing sets with architecture (A-), structure (S-) and plumbing (P-) sheets,
  schedules and details. Questions there are schedule lookups ("On sheet A-801 … width of door 121.A?").
- `habs/`: numbered sheets per historic building; one sheet often holds several views.
- Questions: 76% counts, 20% areas, 3% free text, a few volumes and lengths, in many languages; imperial units
  appear (ft²).

## Concepts

- **Project**: one building. Has levels and sheets. A single uploaded drawing is a one-sheet project.
- **Level**: `{id, name, z_m, height_m}`. Foundation sits below level 0.
- **Sheet**: one drawing payload (today's format, unchanged) plus one or more views.
- **View**: a region of a sheet (`box` in sheet px) with a **kind**, a **discipline** and a **placement**.
  Most sheets have one view covering the sheet. The payload's existing `plans: [{id, box}]` lists regions.
- **Kind**: what the view is (table below). Decides Build steps and how it appears in 3D.
- **Placement**: where the view sits in the building.
  - `plan`: flat at a level; `transform` maps sheet px → project metres.
  - `elevation`: upright, standing on a base line in project metres.
  - `attached`: not in 3D (schedules, details, notes, sections in phase 1).
  - `transform: null` means "not placed yet".
- **Ghost**: a part the building should have but no sheet covers. Drawn see-through with dashed edges, like
  removed walls. Expected parts: per level, architecture, structure and plumbing; plus foundation and roof.
  Shapes are guessed from the footprint (outline of the base plan's walls): a slab under it, strip footings
  along it, a dashed box for a missing level, a roof plate on top.

### Kinds

| Group | Kinds | Build steps | 3D |
|---|---|---|---|
| Plan | `floor_plan`, `foundation_plan`, `structural_plan` (framing / slab), `roof_plan`, `ceiling_plan`, `site_plan`, `plumbing_plan`, `mep_plan` | what is this → scale → walls (floor plans only) → place | image flat at its level; walls raised for floor plans |
| Upright | `elevation`, `section` | what is this → place (elevations) | elevation stands on its base line; section attached in phase 1 |
| Table | `schedule`, `legend` | what is this | attached |
| Other | `detail`, `perspective`, `notes`, `unknown` | what is this | attached |

Each kind maps to a discipline (architecture, structure, plumbing, MEP, site) used for ghost slots.

## Layout

The layout grows out of today's. The left column's single drawing card becomes a project tree; the main area
keeps Drawing | Both | 3D.

- **Project tree**: groups by level (roof at top, foundation at bottom), then *To place* and *Not in 3D*.
  Each row is a sheet (●), a sheet waiting for placement (◐) or a ghost (○). Clicking a row makes it active.
  Clicking a ghost shows an empty-slot card in the 2D panel ("No plumbing plan for Level 1 yet · Add sheet").
- **2D panel**: the active sheet, as today.
- **3D panel**: the assembled building: every placed view, ghosts, the active sheet's layer highlighted.
  Toolbar adds Explode (spread levels apart) next to Height and Fit.
- **Build tab**: steps for the active sheet, chosen by its kind.
- **Ask tab**: covers the project. Citations carry their sheet; clicking one switches the active sheet.
  An answer that needs a missing sheet links to its ghost.

## Place step

Shown for a plan or elevation view whose placement is `null`. The first plan in a project becomes the
**base** and is placed automatically from its confirmed scale, so one-sheet projects never see this step.

1. **Where**: level and kind for a plan, pre-filled from the sheet title and file name.
2. **Match**: click a point on this sheet, then the same point on the base sheet (the 3D panel shows the base
   sheet in 2D during this step); twice. Two pairs fix scale, rotation and offset exactly.
   For an elevation, click the two ends of its ground line, then the two matching corners on the base plan.
3. **Check**: back in 3D, the new sheet sits over the base at half opacity. Confirm or redo.

"Place later" keeps the view in *To place*; it still works in 2D and Ask.

Phase 2: grid lines (`grid` layer) with labels in common are matched automatically, skipping step 2.

## Data contract

### Project manifest

```jsonc
// GET /api/projects → [{id, title, sheets: n, thumbnail}]
// GET /api/projects/{id}
{
  "id": "ac20", "title": "AC20 institute building",
  "source": {"dataset": "Stoa · ifc_bench", "file": "ifc_bench/ac20_arc.ifc", "licence": "KIT, unrestricted use"},
  "levels": [{"id": "F", "name": "Foundation", "z_m": -3.6, "height_m": 0.6},
             {"id": "EG", "name": "Ground floor", "z_m": 0, "height_m": 3.0}],
  "sheets": [
    {"drawing_id": "ac20-eg",
     "views": [{"box": null, "kind": "floor_plan", "discipline": "architecture",
                "placement": {"type": "plan", "level": "EG", "transform": [a, b, tx, ty], "by": "model"}}]}
  ]
}
```

`transform` `[a, b, tx, ty]` is a similarity: `x' = a·x − b·y + tx`, `y' = b·x + a·y + ty` (sheet px →
project metres, y pointing "south" as in the image). Elevation placement:
`{"type": "elevation", "level": "L0", "base": [[x0, y0], [x1, y1]], "sheet_ground": [[u0, v0], [u1, v1]]}`.

A library drawing that is in no project opens as a one-sheet project built in the browser.

### Drawing payload (per sheet, all optional additions)

`kind`, `kind_confidence`, and layers by kind: `walls`, `rooms`, `openings`, `symbols`, `texts` (today);
`footings`, `slab`, `pipes`, `table` (cells with boxes), `grid` (labelled lines), `callouts` (phase 2). The
frontend never requires a layer: a missing one is not drawn.

### Ask

- Request adds `project_id` and `sheet_id`; `drawing_id` stays (the active sheet) so today's backend works.
- Each citation may carry `sheet` (a drawing id). Missing → the sheet that was asked.
- An unanswered question may carry `needs: {kind, level}`. The frontend lights up that ghost. Until the
  backend sends it, the frontend infers it from the question's words when the answer abstains
  (pipes / drain / water → plumbing; footing / foundation → foundation; slab; roof; upper floor → next level).

## Phase 1 (this change)

- Backend: `data/samples/projects.json` + `GET /api/projects`, `GET /api/projects/{id}`.
- Demo project built from the dataset's `ifc_bench/ac20_arc.ifc` (the KIT institute building: basement, three
  floors, attic) by `scripts/build_ifc_project.py`. Basement, ground floor and roof plans and a south elevation
  are rendered in one frame, so they arrive placed (`by: "model"`). The level 1 plan is cropped differently and
  left unplaced, to show the Place step. Level 2, the foundation, structure and plumbing stay ghosts (the model
  has no footings or plumbing).
- Frontend: project tree, per-kind Build steps with "what is this", Place step (plans and elevations),
  assembled 3D with ghosts and Explode, Ask with sheet citations and ghost links, upload adds a sheet to the
  open project.
- User changes (added sheets, kinds, placements) persist in the browser per project (localStorage overlay on
  the manifest). Phase 2 saves them on the server.

## Phase 2

Backend kind classifier and title-block OCR; `footings`, `slab`, `pipes`, `table`, `grid` layers from
teammates' models; grid-label auto placement; sections on cut lines; project-wide QA (one question over
several sheets, `needs` from the router); server-side project saving; IFC upload as a project.

## Edge cases

- Imperial sheets: the scale input accepts ft-in (`12'-6"`) as well as metres.
- No plan in the project: the 3D panel says "Add a plan to start the building" and the view opens Drawing-only.
- Kind guessed wrong: "What is this" is always reachable from Build. Manifest kinds count as confirmed
  (`kindSure` defaults to true); guesses for uploads carry `kindSure: false`.
- Bad placement: Check shows the misfit; Redo clears the points.
- Sheets of different sizes and scales: transforms carry the scale, so nothing assumes a shared pixel size.

## Testing

- `frontend/tests/project.test.ts`: similarity solve from two point pairs (round trip, rotation, scale),
  footprint from walls, ghost slots for a project, `needs` inference, base auto placement, localStorage overlay
  merge.
- `tests/test_api.py`: project list and payload; every sheet in a project resolves to a drawing.
- Browser check: sample project assembles, ghost click shows the empty-slot card, Place step aligns an
  uploaded plan, single-drawing projects behave as before.
