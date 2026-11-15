# Floor-plan PNG → IFC (3D building), DXF, SVG, vector PDF

Research code from the Stoa Lens repo (Junction 2026). It turns a floor-plan image into a 3D building model
(IFC4) plus 2D CAD files. It reads the IFC back and compares it with the image, so you can see what it got wrong.

## Quick start

```sh
python3.12 -m venv .venv && .venv/bin/pip install -r research/stoa-lens/requirements.txt
cd research/stoa-lens
PYTHONPATH=src ../../.venv/bin/python -m stoa_bim.png_to_ifc plan.png out/ --scale 100 --dpi 150
```

`out/` then holds:

| File | What it is |
| --- | --- |
| `model.ifc` | IFC4 building, in metres: IfcWall, IfcDoor, IfcWindow, IfcStair, IfcSpace per room, IfcSlab. Opens in BlenderBIM/Bonsai, FreeCAD, BIMvision, ifcviewer.com. |
| `model.glb` | 3D preview of the IFC (any glTF viewer). |
| `model.dxf` | CAD plan in metres, one layer per element type: WALL, DOOR, WINDOW, STAIR, FIXTURE_<TYPE>, ROOM_OUTLINE, ROOM_NAME, ROOM_NUMBER. |
| `model.svg` | Plan in the PNG's pixel frame; elements tagged (`<g class="door">`, `<polygon class="space">`), scale in `data-m-per-px`. |
| `model.pdf` | Vector PDF at 1:100, one PDF layer per element type. |
| `model.json` | The single source model all the files are written from. |

### Options

- **Scale is required for real sizes.** Pass `--scale 100 --dpi 150` (a 1:100 drawing rendered at 150 DPI), or
  `--m-per-px 0.0169` directly. A wrong scale scales the whole building.
- `--crop x0,y0,x1,y1` limits the plan area to those pixels. Use it to drop the title block and legend.
- `--ocr` uses RapidOCR to keep text out of the walls and to name rooms (`pip install rapidocr`).

A Swiss 1:100 sheet (2480×1753 px) takes about 15–90 s on a CPU.

## What the converter does (`src/stoa_bim/`)

1. **Walls:** thick dark ink after a morphological opening, turned into polygons and extruded to 2.80 m.
2. **Doors and windows:** gaps along each wall run, 0.55–3.6 m wide. A gap with glazing lines becomes a window;
   one with a swing arc or a frame becomes a door. Doors get a head piece of wall, and windows a sill and head.
3. **Stairs:** groups of parallel tread lines.
4. **Rooms:** the free space enclosed by walls and openings, at least 1 m². Each becomes an IfcSpace.
5. **Write** all files from one `model.json`: `writers.py` has `to_ifc`, `to_dxf`, `to_svg`, `to_pdf` and
   `ifc_to_glb`.

Heights are defaults (`model.DEFAULTS`: storey 2.80 m, door head 2.10 m, window 0.90–2.10 m). They are
recorded in the IFC's `Stoa_Assumptions` property set, because a plan carries no elevations.

**From your own geometry:** if you already have walls, doors and rooms as polygons, skip the PNG step. Build a
model dict (see `model.new_model` and any `model.json` from a run), then call
`writers.to_ifc(model, 'out.ifc')`.

**Reading files back:** `ifc_tools.IfcIndex` and `format_readers.py` read the IFC, DXF, SVG and PDF back into the
same index. That gives you rooms with areas, element counts by type and boxes in pixels.

## How good it is

Measured on 42 Swiss validation sheets against the source CAD:

- **Walls:** IoU 83.4.
- **Doors and windows:** F1 74.8 and 87.2.
- **Rooms:** F1 96.3, with 80% within 2% of their area.

Those numbers use the full research pipeline. It adds a fine-tuned door, window and fixture detector and a
trained room segmenter. Neither model is included here, and both expect inputs from the research datasets.
Without them, `png_to_ifc` uses the classical parts only. It finds no fixtures (toilets, basins), and its rooms
come from closed wall loops.

**Known limits:**

- Single storey only.
- Curved walls come out as polylines.
- Doors at wall corners are often missed.
- Scans and photos of drawings work much worse than clean CAD exports.

## What else is here

- **`src/stoa_bim/` research scripts:** `generate.py`, `repair.py` (iterative repair against the PNG),
  `phase0.py`, `phase1.py`, `qa_ifc.py` (question answering from the files with DeepSeek), `cubicasa_model.py`,
  `examples.py`, `iter_assets.py`.
- **`src/stoa_iterative/`:** the room-graph experiment. It segments rooms, flags disagreements with the image,
  and repairs them with rules or with DeepSeek (Verda API).
- **`experiments/iterative-structuring/`:** that experiment's plan, progress log and results page.
  `iterative-structuring.html` and its `worked/` assets show each repair step. `difficult_inputs/` holds hard
  test drawings and their outputs:
  - a US Army Corps of Engineers standard-design piping plan
  - a UK Department for Education sample school plan

  Both are published sample designs, kept here for testing only.

The research scripts import `stoa_semseg` and expect the dataset layout of the Stoa Lens repo, so they do not run
from this folder alone. `png_to_ifc`, `generate`, `model`, `writers`, `section`, `compare`, `ifc_tools` and
`format_readers` do. Calling DeepSeek requires `DEEPSEEK_4_1_FLASH_API_KEY` in a `.env` file. No key is
included.

**Data licences:**

- CubiCasa5K: CC BY-NC-SA 4.0.
- Swiss Dwellings: CC BY 4.0.
- ResBIM (via Junction): MIT.
- Junction's archive: "for non-commercial use in the Junction challenge".
