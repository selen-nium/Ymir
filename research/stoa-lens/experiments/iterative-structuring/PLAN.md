# Iterative structured-plan generation from PNG: plan

2026-11-14 · status: **plan, not started** · builds on [room segmentation trials](../semseg-trials/RESULTS.md)

## 1. Goal and hypothesis

Turn a floor-plan PNG into structured room data, then repeatedly check that data against the PNG and repair what disagrees:

1. **Generate:** a PNG → room tool (from the semseg trials) produces a first version, with or without DeepSeek's help.
2. **Compare:** a comparator renders the structured data back onto the PNG's pixel frame and finds where the two disagree.
3. **Repair:** DeepSeek V4.1 Flash sees the PNG, the current structured data and the flagged regions. It calls the tools again on those regions and proposes edits.
4. **Repeat** steps 2–3 until nothing is flagged, nothing improves, or the iteration cap is reached.

**Hypothesis.** The PNG → structure tool is the weak point. In the semseg trials, the best model still misses 4% of Swiss rooms and 17–22% of CubiCasa rooms, and one missed room breaks a floor total. Most of those failures are visible in the PNG alone: an enclosed white region with no room, a room leaking through a wall, a room number with no room around it. A loop that finds and repairs them should raise accuracy without any new training.

**What would falsify it:** the loop does not beat the same tools re-run on the flagged regions *without* DeepSeek (section 6, arm D), or edits make things worse as often as better.

**Golden goal.** Structured data that is as faithful as possible to the original PNG (target: 99.99%, defined per layer in section 9), and that serves two consumers:
- a **knowledge graph** for question answering (counts, areas, adjacency, "which rooms belong to apartment B")
- a **human engineer in the loop** who validates and corrects it

More than one structured representation may be used if each one adds value (section 5.4).

## 2. The loop, concretely

State after iteration *k*: a **room graph** `G_k` (section 5) in the PNG's pixel frame.

```
G_0  = generator(PNG)                              # one of the tools, optionally DeepSeek-assisted
for k in 1..n_max:
    D_k  = comparator(PNG, render(G_{k-1}))        # ranked disagreement regions, PNG-only (no reference)
    if D_k is empty: stop
    ops  = DeepSeek(PNG crops of D_k, overlay of G_{k-1} on PNG, compact G_{k-1}, tool results)
    G'   = apply(validated ops, G_{k-1})
    if score(D(G')) is not better than score(D_k): revert, stop   # monotone acceptance
    G_k  = G'
```

The comparator never sees the reference. Reference outlines are used only to *score* each `G_k` (sections 8–9), exactly as in the semseg trials.

## 3. How DeepSeek uses the tools (research question 1)

### 3.1 Recommendation: native function calling in a host-run loop, not MCP

| Option | What it is | Fit here |
| --- | --- | --- |
| **Native function calling** (OpenAI-compatible `tools` / `tool_calls`) | The model returns structured tool calls; our Python process executes them and sends results back | **Recommended.** Everything lives in one process: the models, the geometry code, the scorer. It has the fewest moving parts, and every call can be logged, budgeted and replayed. The repo's existing DeepSeek transport already requires a route with `tools` and image input (`src/stoa/harness_transport.py`). |
| **MCP server** | A separate server exposes tools over the Model Context Protocol to any MCP client | Only useful if other agents (Claude Code, a UI) need the same tools. MCP is a protocol between a client and a tool server; it does not make DeepSeek better at using tools. The tool functions are written once and can be wrapped as an MCP server later at no cost. |
| **Prompted JSON, no tool calls** | The model writes a JSON list of edit operations; the host runs any tools first and puts the results in the prompt | **Fallback** if the endpoint lacks tool calls, or tool-call reliability is poor. It is also arm C's control: same information, no tool choice. |

**Lesson from this repo's history.** Earlier DeepSeek + geometry-tool runs (room-area pilot, VISTA workflows) often failed on transport or format, or returned answers not tied to a measurement. So the host stays in charge:

- **Calls:** at most 3 per iteration, at most 2 tool calls per response.
- **Edits:** typed operations only, never free-form geometry rewriting. Every operation is validated before it is applied: valid polygon, inside the plan crop, no overlap with other rooms above 2% of the smaller room.
- **Failure and replay:** a failed call or an invalid operation leaves `G` unchanged. All requests, responses, costs and tool outputs go to the ledger, so a run can be replayed without paying again.

### 3.2 Tools exposed to DeepSeek

All tools are deterministic or seeded, run locally, and work on pixels only.

| Tool | Does | Backed by |
| --- | --- | --- |
| `view_region(bbox, overlay=true)` | Returns a zoomed crop of the PNG, optionally with the current rooms drawn on it | PIL |
| `segment_region(bbox, model)` | Re-runs a segmenter on a crop at a higher zoom. Returns candidate room polygons in page coordinates | RF-DETR-Seg-L / EoMT-L fine-tuned, Raster2Seq, classical |
| `grow_room_from_seed(x, y)` | Floods free space from a point and stops at walls; door gaps are bridged with a fixed radius. Returns one polygon | `geometry.snap`, classical |
| `snap_room(room_id)` | Refits one room's edges to the drawn walls | `geometry.snap` |
| `read_text(bbox)` | OCR words and boxes in a region (room numbers, names, printed areas) | PaddleOCR or Tesseract (PNG only) |
| `room_stats(room_id)` | Area in px² and m² (if scale known), labels inside, neighbours, overlap with ink | Shapely |

**Edit operations** (what DeepSeek can change):

| Operation | Effect |
| --- | --- |
| `add_room(polygon or from_tool_result)` | Add a room |
| `delete_room(id)` | Remove a room |
| `split_room(id, line)` | Cut one room into two along a line |
| `merge_rooms(ids)` | Join rooms into one |
| `replace_room(id, from_tool_result)` | Swap a room's outline for a tool's candidate |
| `set_label(id, type, number)` | Set a room's type and number |

DeepSeek chooses what to do; the tools produce the geometry. This plays to a VLM's strengths (judging what is a room, reading labels, deciding which candidate is right) and avoids its weakness (writing exact coordinates).

### 3.3 Endpoint (blocking)

The repo's DeepSeek runs used `deepseek/deepseek-v4.1-flash-1017` via OpenRouter (`OPENROUTER_API_KEY`), pinned to the Particle AI route, within one shared US$5 ledger. `DEEPSEEK_4_1_FLASH_API_KEY` in `.env` is neither an OpenRouter key (`sk-or-…`) nor a standard DeepSeek API key (`sk-…`). Its base URL is needed before any call, and that endpoint must support image input and tool calls. See section 12.

## 4. Comparator (research question 2)

### 4.1 Why not a Welch PSD here

A Welch power spectrum summarises how much energy sits at each spatial frequency over a whole signal. That suits spectrograms, but not this job, for three reasons:

- **No location:** it is global and ignores position, so it cannot say *where* the PNG and the structure disagree, which is the comparator's whole job.
- **Different styles:** a rendered room graph (filled polygons) and a line drawing (thin ink, text, hatching) have different spectra everywhere, even when the rooms are perfect.
- **Insensitive to big errors:** a missing room changes the spectrum very little.

A local version (spectra per tile) gains location but keeps the style gap. Run it as one ablation (4.4) for comparison with your other work.

### 4.2 Recommended: a disagreement map built from checks that work on the PNG alone

Each check flags pixel regions. Regions are merged, scored and ranked, and the top *k* (default 5) go to DeepSeek with a reason code.

| # | Check | Finds | How |
| --- | --- | --- | --- |
| C1 | **Unclaimed enclosed space** | Missed rooms | Free-space components (white space bounded by ink, door gaps bridged as in the classical baseline) that no room covers, above a minimum area |
| C2 | **Ink crossing** | Rooms leaking through walls, merged rooms | Thick ink, i.e. walls (morphological opening of the ink mask), lying inside a room polygon |
| C3 | **Boundary misfit** | Shifted or rounded outlines | Chamfer distance between each room's edge and the nearest ink line (distance transform of the ink mask). Large mean or max distance is flagged |
| C4 | **Label consistency (OCR)** | Missed, merged or split rooms | Every room-number token must lie in exactly one room. A room holding two numbers is a merge; a number in no room is a miss; a room with no number is a possible split or false room |
| C5 | **Printed-area check (OCR)** | Wrong outlines, wrong scale | Where a room prints its area ("15.61 m²"), compare it with the computed area. This applies to the 20% of Swiss rooms with printed areas, and is reported separately because it uses printed text |
| C6 | **Topology** | Invalid structure | Overlapping rooms, slivers, holes, self-intersections, rooms outside the plan crop |
| C7 | **Tool disagreement** | Uncertain regions | Two segmenters (e.g. RF-DETR and EoMT) disagree, i.e. matched IoU below 0.8 or a room present in one only. This needs no reference and localises the hard places |
| C8 | **Unclosed or broken geometry** | Rooms that silently stop being rooms | Rooms still invalid after the heal step (5.3): fewer than 3 distinct corners, unclosed, self-intersecting, split into fragments, or with gaps or overlaps against neighbours above tolerance. Each is flagged for re-segmentation or `grow_room_from_seed` instead of being dropped |
| C9 | **Unexplained ink (L3)** | Anything drawn but not represented: missed walls, rooms, openings, fixtures, text | The structure (L1 + L2) is rendered as a line drawing in the PNG's style. Ink in the PNG that is not near any rendered element (distance transform, tolerance about 1.5× the line width) is grouped into components and ranked by size. Known decoration (hatching, legend, title block) is learned and ignored on validation |
| C10 | **Knowledge-graph rule violations (L4)** | Errors only visible as broken building logic | Rules such as: every apartment reachable from its entrance through doors; no door leading nowhere; every bathroom has a toilet or washbasin; room areas within an apartment sum to its printed total. Each violated rule points at the rooms or doors involved |

Score per region: area-weighted, with reason weights set on Swiss validation. Total disagreement `D = Σ region scores` drives monotone acceptance and the stop rule.

### 4.3 Learned and perceptual comparators (ablations, not the core)

| Method | Note |
| --- | --- |
| SSIM / MS-SSIM map between the PNG and a line rendering of the structure | Cheap, local. Weak because of the style gap (text, hatching, symbols are not in the structure) |
| LPIPS, or DINOv2 patch-feature distance | Learned similarity that tolerates style somewhat. Untested on drawings; needs a style-matched rendering |
| Learned error detector | A small segmenter trained on validation pairs (prediction overlay → error mask). Could beat the hand-made checks, but needs training data from the semseg predictions; a later phase |

### 4.4 How the comparator itself is judged

On Swiss validation, where references exist: flagged region vs true error (missed room, false room, merge, split, or area error above 2%).

- **Precision and recall of flagged regions:** reported per check C1–C7 and for the combined map.
- **Ablation:** the tile-wise PSD check is run here too, for comparison with your other work.
- **Oracle comparator (upper bound only):** flags the true errors from the reference. It shows how much the loop could gain with a perfect comparator, separating comparator quality from repair quality.

## 5. Structured data format (research question 3)

Sections 5.1–5.3 compare single formats and choose the canonical one. Section 5.4 extends this to several linked layers, each kept only if it adds value.

### 5.1 Comparison

| Format | LLM can read and edit it | Render back to pixels for comparison | Room semantics (id, type, number, area) | Geometry validity under edits | Ecosystem and export | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| **JSON room graph** (GeoJSON-like, pixel frame) | Best: compact, typed, editable by operation | Trivial (our rasteriser / SVG) | Native fields | Checked per operation | Converts to all below | **Working format** |
| **SVG** | Good to read. Writing raw path strings invites broken geometry | Native (resvg / cairosvg) | Only via attributes or classes, e.g. `IfcSpace` classes in Junction's IFC SVGs | Weak | Browsers, CAD import | **View and debug format**, generated from JSON |
| **DXF** | Verbose group-code text; poor for an LLM | Needs a renderer (ezdxf) | Only via layers and text, as in Junction's `ROOM_OUTLINE` / `ROOM_TEXT` | Weak | CAD-native, metric | **Export** for CAD users; matches Junction's own DXF layers |
| **Vector PDF** | Not editable in practice | Native | None (drawing only) | n/a | Printing | Output only |
| **IFC / BIM** | Very verbose: STEP, entity IDs, placements, storeys, 3D | Needs IfcOpenShell | Rich (`IfcSpace`, `IfcWall`, `IfcDoor`) | Strict schema; easy to break | BIM-native, quantity take-off | **Final export** (`IfcSpace` + storey) for BIM use; never the loop's working format |

### 5.2 Recommendation

The **working format is a JSON room graph in the PNG's pixel frame**. SVG is rendered from it for viewing, and it exports to DXF and IFC.

```json
{"frame": {"image": "…png", "width": 2480, "height": 1753, "m_per_px": null, "scale_source": null},
 "rooms": [{"id": "r12", "polygon": [[x, y], …], "type": "bedroom", "number": "2.10", "name": "Zimmer",
            "area_px": 31250.0, "area_m2": null, "source": "eomt-finetuned", "confidence": 0.93,
            "history": ["add@0", "snap@2"]}],
 "walls": [], "openings": [], "text": [{"text": "2.10", "bbox": […]}],
 "iteration": 2, "disagreement": 0.031}
```

Why:
- **Easy to compare:** it renders back to the exact pixel frame.
- **Easy to edit safely:** typed operations, each validated.
- **Scored as is:** the semseg scorer reads this format already.
- **Rich enough to export:** it has everything an `IfcSpace` export needs (outline, storey, name, number, net area once scale is set).

Walls and openings are optional in phase 1, because rooms carry the area answers. They are added in phase 3 if C2 and C3 show wall structure would help.

### 5.3 Closed, valid geometry: the heal step

A room only counts if it is a closed, valid region. Broken geometry comes from several places:
- **Line-based formats:** a room exists only if wall lines close a loop. Door gaps and endpoints that nearly meet stop the loop closing, and the room silently disappears. Junction's own DXF has 2-point `ROOM_OUTLINE` polylines.
- **Raster2Seq:** sequences cut off mid-room by the token limit.
- **Masks:** fragmented masks, pinholes, and slivers between neighbours.
- **Exports:** a DXF polyline without the closed flag, an SVG path without `Z`, an `IfcSpace` with an unclosed profile.

Rules:
1. **Rooms are stored as closed polygons, never as wall lines.** Closure is a property of each room, not something inferred from lines meeting.
2. **A deterministic heal step runs on every graph version**, after the generator and after every applied operation, before scoring:
   - close rings
   - drop repeated or collinear vertices and spikes
   - `make_valid` / `buffer(0)` for self-intersections
   - fill pinholes below a minimum area
   - keep the largest fragment and hand the rest back as candidates
   - snap shared edges to a common boundary, so neighbours within tolerance get no gaps and no overlaps (a planar partition)
   - remove slivers below a minimum width
3. **Whatever the heal step cannot fix is flagged by C8** and repaired from the image (re-segmentation or seed growth), never silently dropped.
4. **Exports are checked by re-reading them.** After DXF / SVG / IFC export, re-import with ezdxf / an SVG parser / IfcOpenShell, check that every room comes back as a closed region with the same area (within 0.5%), and count failures.
5. **If walls are ever used to build rooms** (phase 3, or vector inputs), polygonise the wall network after snapping endpoints within a tolerance, and bridge door openings with the same fixed radius as the classical baseline. The share of rooms that close is reported.

Reported per tool and per iteration: invalid geometries before and after healing, rooms lost to unfixable geometry, export round-trip failures.

### 5.4 Several linked layers, one source of truth

No single format serves faithfulness, question answering and human review at once. So the design uses **one canonical store and several derived layers**.

- **Single writer:** only the canonical layer is edited, by DeepSeek, the tools or a human. Every other layer is regenerated from it deterministically, so layers can never disagree.
- **Value test for each layer:** a layer stays only if it measurably helps one of three things: (a) the comparator, which finds more true errors with it; (b) knowledge-graph answers; (c) review speed or accuracy. Layers are switched on one at a time (section 6.1) to test this.

| Layer | Contents | Representation | Who uses it | Value it must show |
| --- | --- | --- | --- | --- |
| **L1 Geometry (canonical)** | Rooms as faces of a **planar graph**: wall centreline segments as edges, junctions as nodes, rooms as the closed faces between them. Openings (doors, windows, with swing) lie on wall edges. Fixtures are symbols with type and box. Each element keeps pixel coordinates, source tool, iteration, confidence and edit history | Typed JSON (rooms + walls + openings + fixtures), pixel frame plus scale | Loop, scorer, all exports | Closure and adjacency by construction: neighbouring rooms share an edge, so gaps and overlaps cannot occur (5.3). Doors become connections between rooms |
| **L2 Text and annotation** | Every text token in the PNG (room numbers, names, printed areas, dimension strings, title block, scale text, legend), with box, OCR confidence and the element it is bound to (room, wall, sheet) | JSON, linked to L1 ids | Comparator (C4, C5), scale reading, room identity, KG labels | Finds merged, missed and mislabelled rooms; supplies names and numbers that questions use |
| **L3 Drawing residual** | Everything in the PNG not yet explained by L1 + L2: the rendered drawing is subtracted from the PNG's ink, leaving residual ink grouped into components (unknown symbols, hatching, dimension lines, missed walls) | Raster mask + component list | Comparator (fidelity), reviewer | Turns "99.99% faithful" into a measurable quantity: explained ink fraction. Each residual component is either explained (a new element) or marked as known decoration |
| **L4 Knowledge graph** | Building → storey → apartment → space → door / window / fixture; adjacency, door connectivity, containment, labels, areas. Every fact carries its evidence: L1/L2 ids and a pixel box | Property graph (JSON-LD) using the W3C **BOT** (Building Topology Ontology) plus a small extension for drawing evidence | Question answering, reasoning checks | Answers Junction-style questions by graph query (sum areas of spaces in apartment B; count doors of a room), with a pixel citation for every answer |
| **L5 Review view** | The PNG with toggleable layers (rooms, walls, openings, text, residual, flagged regions), coloured by confidence. A queue of only the flagged or low-confidence items. Accept, reject or edit actions recorded as the same typed operations the VLM uses | Layered SVG + a small web page | Human engineer | Reviewer minutes per sheet, and errors caught per minute, against reviewing the raw PNG |
| **L6 Interchange exports** | DXF (layers as in Junction: `ROOM_OUTLINE`, `ROOM_TEXT`, `WALL`, `DOOR`, `WINDOW`), IFC (`IfcSpace`, `IfcWall`, `IfcDoor`, `IfcWindow` per storey), GeoJSON | Generated files, round-trip checked (5.3) | CAD and BIM tools, other teams | Opens correctly in CAD/BIM; re-imported rooms match L1 (area within 0.5%) |

**How the layers make the loop stronger:**
- **L1 as a planar graph** removes the unclosed-geometry problem at its source (5.3), and gives the knowledge graph its adjacency.
- **L2** makes the label checks (C4, C5) possible, and later scale and room identity.
- **L3** adds the most general comparator check, C9 (unexplained ink): any drawn thing the structure does not account for is flagged. This is the closest match to "find where the PNG and the structure disagree" in your original idea.
- **L4 enables reasoning checks** the image alone cannot give:
  - every apartment reachable from its entrance through doors
  - every bathroom holds a toilet or a washbasin
  - room areas sum to the apartment area where it is printed

  A failed rule is a new flag for the loop.
- **L5** turns human corrections into new reference data for the comparator and later training.

**Build order:**
1. L1 with rooms only, plus L2 (phases 0–3).
2. Walls and openings in L1, plus L3 (phase 4).
3. L4 and L5 (phase 5).
4. L6 exports from phase 0, because they are cheap and their round trip is a useful check.

## 6. Experiment design: every tool as the generator

The tool's output is the weak point, so each tool is run as the starting generator, through the same loop.

| Generator `G_0` | Notes |
| --- | --- |
| EoMT-L, fine-tuned | Best on Swiss (Room F1 95) |
| RF-DETR-Seg-L, fine-tuned | Best on CubiCasa (F1 81) |
| Raster2Seq, best of base and fine-tuned | Native polygons; 256 px input, which re-segmenting at a higher zoom on a crop may help most |
| Classical (no learning) | Weak start (F1 42): shows how far the loop alone can go |
| DeepSeek alone (no segmentation tool) | Control: the VLM proposes rooms by seed points, using only `grow_room_from_seed` |

**Arms per generator:**

| Arm | Description | Tests |
| --- | --- | --- |
| A | `G_0` only (n = 0) | Starting point; equals the semseg results |
| B | Loop, PNG-only comparator, DeepSeek + tools | The proposal |
| C | Loop, DeepSeek with prompted JSON (no tool calls); host pre-runs `segment_region` and `grow_room_from_seed` on flagged regions | Does tool *choice* by the model matter? |
| D | Loop **without DeepSeek**: a fixed rule per reason code re-runs tools on flagged regions | **Key control: is the VLM needed at all?** |
| E | Loop, **oracle** comparator, DeepSeek + tools | Ceiling for the repair step |

Run order is cost-aware: arm D first (free); then B, C and E on Swiss validation with two generators (EoMT, Raster2Seq); then the full grid only for arms that beat D on validation.

### 6.1 Which layers earn their place

With the best generator and arm B fixed, layers are switched on one at a time on validation:

| Variant | L1 | L2 text | L3 residual | L4 rules | Question |
| --- | --- | --- | --- | --- | --- |
| R1 | Rooms only (polygons) | — | — | — | Baseline representation |
| R2 | Planar graph (rooms + walls + openings) | — | — | — | Does a topological model reduce broken geometry and improve repairs? |
| R3 | R2 | Yes | — | — | Do label checks catch extra errors? |
| R4 | R3 | Yes | Yes | — | Does the unexplained-ink check find what C1–C8 miss? |
| R5 | R4 | Yes | Yes | Yes | Do building-logic rules add flags that are true errors? |

A layer is kept if it raises comparator recall at the same precision, or the final accuracy, by more than the paired bootstrap interval. Otherwise it is dropped from the loop, but may stay as an export.

## 7. Number of iterations (research question 4)

- **Cap `n_max = 4`**, with **early stop**: stop when no region is flagged, or when an iteration fails to lower `D` by at least 2% (that iteration is then reverted, by the monotone-acceptance rule).
- **Why 4:**
  - Iterative self-refinement with language models usually gains most in the first 1–2 rounds and levels off by 3–4. Later rounds tend to oscillate or regress unless edits are gated.
  - Gains here are bounded by the comparator. Once C1–C7 stop flagging, more rounds have nothing to act on.
  - Cost grows linearly with n (section 10).
- **How n is chosen:**
  - Plot the curve n = 0…4 (Room F1, rooms within 2%, Junction questions within 2%, cost) on Swiss validation.
  - Fix the final rule there: a hard cap or early stop only.
  - Report the full curve once on test.
  - If validation still improves at 4, extend to 6 on validation only before touching test.

## 8. Data and splits

| Set | Use | Notes |
| --- | --- | --- |
| Swiss validation (42 sheets) | Development: comparator weights, n, prompts | Already prepared, references exact |
| Swiss test (50 sheets, 287 Junction area questions) | Final, run once | Same as the semseg trials |
| CubiCasa clean subset (61) + validation (77) | Real scans | Pixel-area references; human outlines (see semseg §6) |
| **IFC plan set (56 drawings, 2,218 area questions)** | New external test, never seen by any model | Reference outlines from the `IfcSpace` paths in each plan SVG; needs a preparation step and an alignment check like Swiss's |
| AECV-Bench / AEC-Bench | Not usable for scoring (no room outlines or scale; semseg §7 and §9). May supply qualitative examples only | Per AGENTS.md, recorded as a coverage gap |

## 9. Metrics

- **Per iteration** (n = 0…4): Room F1, precision, recall, room-type F1, rooms within 2% / 5% area, median area error, rooms answered, and Junction area questions within 2% by family. The scorer is the semseg one, unchanged.
- **Faithfulness, the "99.99%" target, defined per layer.** A single pixel-level 99.99% is not meaningful: anti-aliasing, line weights and fonts differ even in a perfect structure. Instead:

  | Fidelity measure | Definition | Target |
  | --- | --- | --- |
  | Element recall and precision | Rooms, walls, doors, windows and fixtures matched to references (IoU ≥ 0.5, or within tolerance for lines and symbols) | ≥ 99.99% per element type is the golden goal. Realistically measured on validation, and any shortfall listed item by item |
  | Geometric fidelity | Room area within 0.5% and wall position within 1 px of the reference | Share of elements meeting the tolerance |
  | Label fidelity | Room number, name and type correct | Share of rooms |
  | Explained ink | Share of PNG ink pixels within tolerance of a rendered element or of known decoration (L3) | Approaches 100%; the remaining ink is listed for review |
  | Topology fidelity | Room adjacency and door connectivity match the reference (graph edit distance) | Share of edges correct |
  | Question fidelity | Junction questions answered from L4 by graph query | Within 2% (Junction's rule) |

  Reaching 99.99% on a sheet with about 30 rooms means zero room errors on almost every sheet. That is why the human review layer (L5) is part of the goal, not an add-on: the loop aims to leave the reviewer only a short list of flagged items.
- **Edit accounting:** operations proposed, valid, applied, reverted; regions flagged and fixed.
- **Comparator quality:** section 4.4.
- **Cost and latency:** calls, tokens, US$, seconds per sheet per iteration.

## 10. Budget

The cost per call depends on the endpoint's prices (section 12). The plan below caps calls, not dollars, until those prices are known.

- **Calls:** per sheet per iteration, at most 3 DeepSeek calls, with images as crops (at most 5 regions, about 768 px each) plus one overview.
- **Development:** 2 generators × 3 paid arms (B, C, E) × 42 validation sheets × up to 4 iterations × 3 calls ≈ 3,000 calls worst case. Early stop typically cuts this by half or more.
- **Test:** only the arms and generators that win on validation.
- **Ledger:** every call reserves a conservative cost before dispatch, as in the existing transport (uncertain billing keeps the reservation), and the run stops at the cap.

## 11. Phases and gates

| Phase | Work | Gate to continue |
| --- | --- | --- |
| 0 | Room-graph format, renderer, operation validator, IFC plan-set preparation | Reference graphs round-trip: reference → graph → render → area matches (98%+ within 2%, as in the semseg geometry ceiling) |
| 1 | Comparator C1–C7 + PSD ablation; precision/recall on validation | Combined map finds at least 60% of true errors at precision of at least 50% on validation |
| 2 | Arm D (no VLM) for all generators | A curve exists; shows how much tools alone recover |
| 3 | Endpoint check (images + tools); arms B, C, E on validation (2 generators) | B beats D on validation for at least one generator |
| 4 | Full grid on validation; choose n and arms | Frozen configuration |
| 5 | Test once (Swiss test, CubiCasa clean, IFC set); RESULTS.md | — |

## 12. Assessment: is this worth doing, and what will be hard

### 12.1 Overall view

**Worth doing, with a narrower aim than "iterate until the PNG and the structure match".**

**Where the loop should pay off: missed rooms.** One missed room breaks an apartment or floor total. That is why fine-tuned EoMT-L answers 89% of Swiss apartment questions but only 59% of floor questions. Missed rooms leave strong PNG-only evidence: an enclosed white region with no room (C1), or a room number with no room around it (C4). Both are easy to flag and easy to repair with a seed-point flood fill or a zoomed re-segmentation.

**Where it probably won't pay off: small boundary errors.** The best models already reach about 1% median area error on Swiss. A PNG-only comparator cannot reliably see a 1–2 px shift, and a VLM cannot place a vertex that precisely. Boundary precision is better handled by snap and higher-resolution re-segmentation than by the VLM.

**Biggest open question: does DeepSeek add anything over deterministic repair?** Fixed rules ("empty enclosed region → grow a room from its centre") may recover most of the gain at no cost. Arm D tests exactly this, and is run first. The VLM's likely value is arbitration on ambiguous cases: is this region a shaft or a storeroom; should these two candidates merge; which tool's outline is right.

**Expected headroom by starting point:**
- **Small for EoMT-L on Swiss** (F1 95), though worth it for floor totals.
- **Large for weak generators** (classical F1 42, Raster2Seq F1 59–63) and for **CubiCasa** (17–22% of rooms unanswered).
- **Unknown for the IFC set.**

**Reframe:** a **verify-and-repair loop**. Deterministic checks find problems, tools produce candidate fixes, DeepSeek arbitrates where the rules cannot decide, and every edit must lower a multi-part disagreement score. This is the design in sections 3–4. The table below lists what has to be overcome.

### 12.2 Challenges and how to overcome them

| # | Challenge | Why it matters | How to overcome it |
| --- | --- | --- | --- |
| 1 | **The VLM may add little over fixed rules** | If arm D (no DeepSeek) matches arm B, the API cost and complexity buy nothing | Run arm D first and treat it as the bar. Give DeepSeek only the regions where rules disagree or abstain. Report B − D per error type |
| 2 | **"Room" is a convention, not a visual fact** | Shafts, voids, stair cores, lift wells, courtyards and outside space all look like enclosed white regions. Junction excludes some from totals, and CubiCasa splits open-plan spaces by annotator judgement. C1 would flag them as "missed rooms" and the loop would add false rooms | Per-dataset ignore rules, learned on validation: minimum area, hatched or crossed fill (shafts, lifts), touches the plan boundary. Let DeepSeek label a candidate "not a room". Measure precision of added rooms separately |
| 3 | **Optimising the comparator instead of accuracy (Goodhart)** | The loop can lower the disagreement score without getting closer to the truth: deleting doubtful rooms removes their flags, and over-growing rooms removes "unclaimed space" | Multi-part score with guards: an iteration may not lower room count unless C4 supports it, may not raise ink-crossing, and must keep every OCR room number covered. Track the real metrics against references on validation at every iteration, and stop if they diverge from the comparator score |
| 4 | **VLMs are poor at exact coordinates** | Free-form polygons from the model would be imprecise and often invalid | DeepSeek never writes geometry. It picks operations and seed points, and tools produce the polygons. Crops come with a coordinate grid and numbered candidates, so the model answers "candidate 2" or "seed at grid C4" |
| 5 | **Comparator blind spots** | Errors with no PNG evidence are invisible: open-plan splits, a room drawn slightly too large along a thin wall, a wrong room type | Accept these as limits and measure them. On validation, the oracle comparator (arm E) shows the share of errors the PNG-only comparator cannot see. Fix wrong room types with OCR names (C4) where names are printed |
| 6 | **OCR at 150 DPI** | Swiss room numbers and areas are small: about 10–14 px tall in the 150 DPI PNG (4.6–6.6 pt in the PDF). OCR misses or misreads would create false flags in C4/C5 | Run OCR on 2–3× upscaled crops. Accept only tokens matching expected patterns (`\d+\.\d{2}`, `m²`). Weight C4/C5 by OCR confidence. Measure OCR recall on validation, where the true room numbers are known |
| 7 | **Oscillation and error compounding** | An edit at iteration 2 can undo a good edit from iteration 1, or a bad merge can spread | Monotone acceptance (revert any iteration that does not lower the score). A tabu list of regions already fixed or rejected. Each room's edit history kept in the graph. Hard cap n_max = 4 |
| 8 | **Tool-call reliability and transport failures** | This repo's earlier DeepSeek tool loops often failed on format, timeouts or provider rejections | Host-validated operations with a strict schema, and one repair prompt for invalid JSON. A failed call leaves the graph unchanged and is logged as a failure, never silently retried. The prompted-JSON fallback (arm C) covers an endpoint without tools |
| 9 | **Unknown endpoint, cost and rate limits** | The key's provider is unidentified, and pricing sets how much of the grid can run | Phases 0–2 need no API. Then a 5-sheet smoke test that measures tokens, cost, latency and tool support. Then a hard ledger cap with conservative reservations before dispatch, as in the existing transport |
| 10 | **Token cost of large sheets** | Full Swiss sheets are 2480 × 1753 px, and several images per call add up | Send one low-resolution overview plus at most 5 region crops (about 768 px each). Send the room graph as compact JSON (ids, bboxes, areas), not full polygons. Cache tool results |
| 11 | **Non-determinism** | VLM outputs vary between runs, which makes small gains hard to trust | Temperature 0 (or the lowest allowed). Fixed prompts and seeds. Keep every response so runs can be replayed without paying again. Report paired bootstrap intervals per sheet; the test sets are small (50 Swiss sheets) |
| 12 | **Little headroom on the best Swiss model** | EoMT-L already reaches Room F1 95, so gains there will be a few points and close to noise | Read results per generator. The headline is the weak-generator and CubiCasa/IFC gains, plus Swiss floor totals, where single misses dominate |
| 13 | **Leakage into tuning** | Comparator weights, ignore rules, prompts and n are all tuned. Tuning on test would inflate results | Tune everything on validation only. Freeze the configuration (phase 4). Run test once. The IFC set is a fresh external test that no tuning sees |
| 14 | **Synthetic Swiss style** | Swiss sheets come from one renderer, so checks tuned there (hatching, line weights) may not transfer | Tune ignore rules per dataset. Report the IFC set and CubiCasa separately. Keep thresholds relative (to line width, median room size) rather than absolute pixels |
| 15 | **Scale and room identity are still outside the loop** | A perfect room graph still needs metres per pixel and the right room for each question to answer Junction's area questions from a PNG | Keep the true scale for the core study, as in the semseg trials. Add scale reading (scale bar, "1:100") and OCR room identity as an optional loop task in phase 4, reported as a separate end-to-end row |
| 16 | **Latency for the full archive** | Several calls per sheet per iteration is slow for 14,000 drawings | Early stop typically ends most sheets after 1–2 iterations. Run DeepSeek only on sheets whose comparator score crosses a threshold. Measure seconds per sheet; arm D may be enough for bulk runs |
| 17 | **Unclosed or broken geometry** | A room drawn as a ring that does not close, a polygon with fewer than 3 corners, a self-intersecting outline, or gaps and overlaps between neighbours is not treated as a room by area tools, CAD or BIM import. It silently becomes a miss | Store rooms as closed polygons, not wall lines. Heal every graph version deterministically (5.3). Flag what cannot be healed (C8) for re-segmentation instead of dropping it. Re-import every DXF / SVG / IFC export and check that each room comes back closed with the same area. Report invalid and lost geometry per tool |

## 13. Open questions for you

- [ ] **Endpoint for `DEEPSEEK_4_1_FLASH_API_KEY`:** base URL, model ID, and whether it accepts images and tool calls. It is not an OpenRouter or standard DeepSeek key. Until this is known, phases 0–2 can run (they need no API calls).
- [ ] **Spending cap** for this experiment. Does the existing US$5 OpenRouter ledger apply to this key, or does it get its own?
- [ ] **IFC plan set** as the new external test: yes or no. Adds a preparation step, but it is the only unseen non-Swiss set with exact room outlines.
- [ ] **OCR engine** for C4/C5: PaddleOCR (Apache 2.0) or Tesseract (Apache 2.0). PyMuPDF is excluded because the input is PNG only.
- [ ] **Scale:** keep the true scale, as in the semseg trials, or add scale reading from the PNG (scale bar, "1:100" text) as a loop task.
