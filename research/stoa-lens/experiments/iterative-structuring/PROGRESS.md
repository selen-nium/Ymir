# Iterative structured-plan generation: progress

Plan: [PLAN.md](PLAN.md). This file is updated as each step finishes, so it shows what is done and what is pending.

**Last update:** 2026-11-15 05:05

Explainer page with every arm, method, dataset and iteration: https://claude.ai/artifact/La3cMV8U9DxQ4qEFrjGLMb (source `experiments/iterative-structuring/iterative-structuring.html`).

## 1. Phase status

| Phase | Work | Status |
| --- | --- | --- |
| 0 | Room graph (L1 rooms), heal step, SVG render, DXF / IFC / GeoJSON export with round-trip check | **Done, gate passed** |
| 0 | L2 text layer (OCR on the PNG) | **Done** |
| 1 | Comparator checks C1–C8, PSD ablation, precision/recall against true errors on Swiss validation | **Done** (Swiss). Gate met for missed rooms, not for small area errors (see below) |
| 2 | Arm D: repair loop without DeepSeek, every generator | **Done** for Swiss and CubiCasa (validation, then test once) |
| 3 | DeepSeek arms B, C, E (Verda, `deepseek-4-1-flash-1017`) | **Validation done** for Swiss (2 generators) and CubiCasa (1). B beats D on validation, so the gate is met. **Test running** (§4.13) |
| 2b | **Push to 80%** on rooms within 2% / 5% and Junction questions (room, apartment, label, floor). Validation first, test once | **In progress** (Swiss validation) |
| ctrl | **Control:** DeepSeek answers each Junction question directly from the PNG (no tools, no structure) | **Swiss test strict done**; CubiCasa strict and Swiss estimate running (§4.11) |
| — | IFC plan set (56 drawings) as external test | **Waiting** for your yes (PLAN §13) |
| — | L1 walls/openings, L3 residual, L4 knowledge graph, L5 review view | Later phases (PLAN §5.4) |

## 2. Next: push to 80% (your request)

Target: rooms within 2% and 5%, and Junction room / apartment / label / floor questions, at about 80% or more.

**Why more iterations alone will not get there:** arm D is flat from n = 2 to n = 4, because the loop's rules run out of fixable flags and the comparator cannot see small boundary errors. More iterations help only once some repair keeps finding real fixes.

**Levers, in order:**
1. **Printed areas as a feature.** Legitimate in deployment. Reported as a separate "with printed text" row, never mixed into the segmentation headline.
2. **Sharper outlines:**
   - re-segment each room on a zoomed crop (masks currently come from about a quarter of the input resolution)
   - fuse EoMT and RF-DETR outlines
   - fit room edges to wall lines with sub-pixel precision
3. **The DeepSeek judge (arm B)** for doubtful added or split rooms, which cost apartment and floor answers. Blocked on the endpoint and spending cap.
4. **Extend n** while the validation curve still rises.

**Discipline:** iterate on validation only; run test once at the end; report where each metric stops if it stays below 80%.

## 3. Decisions taken without asking

- OCR engine: **RapidOCR** (ONNX build of PaddleOCR models, Apache 2.0). It was already installed in this repo; Tesseract is not installed.
- Code lives in `src/stoa_iterative/` and runs in `.venv-semseg`, alongside the segmentation tools it calls.
- Generators reuse the semseg trials' saved predictions (validation and test). The starting point `G_0` for each tool equals its semseg result.

- Arm D's repair rules (`src/stoa_iterative/repair.py`):
  | Flag | Repair |
  | --- | --- |
  | C4, room number in no room | Grow a room from the number |
  | C4, two numbers in one room | Split the room between them |
  | C2, wall ink inside a room | Split along the wall |
  | C3, outline far from ink | Snap |
  | C5, printed area doesn't match | Pick the outline option whose area matches the printed value best |
  | C7, other tool has a room here | Add the other tool's room if nothing covers it |
  | C1, empty enclosed space | Add the region, **only with corroboration**: the other tool also sees a room there, or OCR finds a room number or word inside |

  An edit is kept only if it lowers the disagreement score, never uncovers an OCR room number, and never deletes a room (except a supported C4 split).
- Why C1 needs corroboration: in the first smoke test, uncorroborated C1 added 19 "rooms" (shafts, voids) to a 13-room plan. That cleared the flags without adding a single true room, which is the comparator-gaming risk in PLAN §12.2 #3. Uncorroborated empty regions stay flagged; arm B is where DeepSeek judges them.

- **Endpoint** (from Verda's docs, after you named Verda as the provider): `https://inference.verda.com/v1`, model `deepseek-4-1-flash-1017`. Images and tool calls are supported. $0.15 / $0.60 per 1M input / output tokens. Client: `src/stoa_iterative/verda.py`.
- **Spending cap: US$15** on its own ledger (`data/iterative-structuring/verda/ledger.jsonl`). The plan needs about US$12.
- **Rate limit:** the account allows about 60 requests per minute (measured). Calls are paced to 55 a minute across processes. HTTP 429 responses are rejected before processing and not billed, so they are waited out and resent; nothing else is retried.
- **Second control prompt ("estimate"):** under Junction's strict prompt, DeepSeek declines most area questions. The estimate variant forces a number, to test whether it can measure at all.
- **Arm E:** dismissals do not count as progress. The flags are confirmed errors, so only fewer true errors does.
- **CubiCasa count questions** (12 English types) are attached to its pages (backup at `pages.before-count-questions.json`).
  - Bedrooms and kitchens are counted by room class; reference classes give 96% / 100% on validation, OCR labels only 54%.
  - Bathrooms, WC rooms, saunas and balconies are counted by a Finnish OCR label inside a room.
- **All arms re-run with a per-iteration trace** (`src/stoa_iterative/trace.py`) for n = 0–4 on validation and test. The trace records flags by check and kept repairs, and scores every question type at every iteration.

## 4. Results so far

### 4.1 Phase 0: room graph, heal step, exports (validation)

Gate: reference outlines must survive the graph (98%+ of rooms within 2%), and every export must re-read with every room closed and the same area.

| Input → graph → scorer | Dataset | Room F1 raw → graph | Precision raw → graph | Rooms within 2% raw → graph |
| --- | --- | --- | --- | --- |
| Reference outlines | Swiss (42) | 100.0 → 99.9 | 100.0 → 100.0 | 99.9% → 99.8% |
| Reference outlines | CubiCasa (77) | 100.0 → 99.4 | 100.0 → 100.0 | 99.5% → 98.6% |
| EoMT-L fine-tuned | Swiss | 94.6 → **96.4** | 93.7 → **97.1** | 64.6% → 65.3% |
| EoMT-L fine-tuned | CubiCasa | 78.5 → **80.9** | 83.7 → **89.6** | 31.1% → 31.3% |

- **Gate passed.** The small CubiCasa reference loss comes from nested CubiCasa annotations: some rooms are drawn inside others, and the heal step removes overlaps.
- **Healing alone improves a model.** Removing overlaps and stray fragments lifts EoMT's precision at no cost (Swiss F1 +1.8, CubiCasa +2.4). This is iteration 0 of every loop arm.
- **Exports:** DXF, IFC (IFC4, `IfcSpace` with closed extruded profiles, holes as voids) and GeoJSON all re-read correctly on 10 of 10 pages per run, with every room closed and areas within 0.5%. No room was left invalid after healing.

### 4.2 L2 text layer: OCR

RapidOCR on each plan crop, upscaled 2×, about 2 s per Swiss sheet on CPU. Results are cached in `data/iterative-structuring/ocr/`.

| Swiss split | Printed room numbers | Read correctly | Recall | False room-number tokens |
| --- | ---: | ---: | ---: | ---: |
| Validation (42) | 1,457 | 1,329 | 91.2% | 18 |
| Test (50) | 1,470 | 1,372 | 93.3% | 10 |

### 4.3 Phase 1: comparator quality (Swiss validation, 42 sheets)

Each starting tool's healed graph is checked with the PNG-only checks. A flag is correct if its box touches a true error: a missed room, a false room, or a room more than 2% off in area. True errors come from the reference outlines and are used here only to grade the comparator.

**Precision and recall, all checks except PSD:**

| Starting tool | Flags | Correct flags | Errors | Errors found |
| --- | ---: | ---: | ---: | ---: |
| EoMT-L fine-tuned | 434 | 66.6% | 548 | 45.1% |
| RF-DETR-Seg-L fine-tuned | 445 | 81.1% | 709 | 50.2% |
| Raster2Seq base | 2,168 | 98.1% | 1,545 | 89.4% |
| Classical | 2,061 | 92.8% | 2,218 | 90.9% |

**What it finds, by error type:**

| Error type | EoMT-L: found | RF-DETR-Seg-L: found |
| --- | ---: | ---: |
| Missed room | **82%** (51 / 62) | **94%** (84 / 89) |
| False room | 52% (22 / 42) | 65% (45 / 69) |
| Area off by more than 5% | 49% (79 / 160) | 53% (113 / 213) |
| Area off by 2–5% | 33% (95 / 284) | 34% (114 / 338) |

**Per check, EoMT-L as the starting tool:**

| Check | Flags | Correct | Errors found |
| --- | ---: | ---: | ---: |
| C1 unclaimed enclosed space | 52 | 40.4% | 3.3% |
| C2 wall ink inside a room | 21 | 47.6% | 1.6% |
| C3 outline far from ink | 4 | 100% | 1.8% |
| C4 room numbers (OCR) | 48 | 89.6% | 13.7% |
| C5 printed area vs outline (OCR) | 87 | 98.9% | 21.0% |
| C7 disagreement with RF-DETR | 222 | 56.3% | 22.4% |
| C8 broken geometry | 0 | — | — |
| PSD (tile-wise power spectrum, ablation) | 210 | 12.9% | 4.6% |

Findings:
- **The comparator finds missed rooms** (82–94%), the error that breaks floor and apartment totals. This is the case the loop is built for.
- **Small area errors (2–5%) are mostly invisible from the PNG** (33% found), as PLAN §12.1 expected. The loop is unlikely to fix them; snap and higher-resolution re-segmentation are the tools for that.
- **The label checks are the most precise:** C4 room numbers are right 87–99% of the time, and C5 printed areas 98–100%. C5 needs a printed area, which only about 20% of Swiss rooms have.
- **Tool disagreement (C7) has the widest reach**, up to 62–75% of errors for the weak tools.
- **C1 is imprecise on strong models** (40–56%). It flags free space that is not a room: shafts, stair voids, outside courtyards. This is challenge 2 in PLAN §12.2; ignore rules are next.
- **The PSD ablation is the least precise check** (13–24% on the strong models), as predicted in PLAN §4.1.
- **C8 never fires,** because healing leaves no invalid geometry.
- **Phase 1 gate:** 60% of errors found at 50%+ precision. It is met for the weak tools and for missed rooms, but not overall for EoMT-L and RF-DETR-Seg-L, whose remaining errors are mostly small area errors. Phase 2 goes ahead, aimed at missed and false rooms.

### 4.4 Phase 2: arm D, the repair loop without DeepSeek (Swiss validation, 42 sheets), all checks including C5

`n` = iteration; n = 0 is the starting tool's output after healing. Junction questions use Junction's 2% rule, on validation's English area questions. Validation has only 42 sheets, so the question columns are noisy.

| Starting tool | n | Room F1 | Precision | Recall | Rooms within 2% | Rooms within 5% | Q room | Q apartment | Q label | Q floor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EoMT-L fine-tuned | 0 | 96.4 | 97.1 | 95.7 | 65.3% | 84.8% | 76.2% | 84.2% | 76.2% | 64.3% |
| EoMT-L fine-tuned | 1 | 96.4 | 95.5 | 97.4 | 68.2% | 86.3% | 76.2% | 86.8% | 90.5% | 73.8% |
| EoMT-L fine-tuned | 2 | 96.6 | 95.4 | 97.8 | 68.4% | 86.5% | 76.2% | 89.5% | 90.5% | 73.8% |
| EoMT-L fine-tuned | 4 | 96.6 | 95.4 | 97.9 | 68.4% | 86.6% | 76.2% | 89.5% | 90.5% | 73.8% |
| RF-DETR-Seg-L fine-tuned | 0 | 94.5 | 95.2 | 93.9 | 55.9% | 79.1% | 67.9% | 69.7% | 81.0% | 59.5% |
| RF-DETR-Seg-L fine-tuned | 1 | 94.0 | 91.7 | 96.4 | 61.4% | 82.4% | 70.2% | 77.6% | 88.1% | 50.0% |
| RF-DETR-Seg-L fine-tuned | 2 | 94.0 | 91.6 | 96.6 | 61.6% | 82.6% | 71.4% | 77.6% | 88.1% | 50.0% |
| RF-DETR-Seg-L fine-tuned | 4 | 94.0 | 91.6 | 96.6 | 61.6% | 82.6% | 71.4% | 77.6% | 88.1% | 50.0% |
| Raster2Seq base | 0 | 62.2 | 78.6 | 51.4 | 7.6% | 16.6% | 8.3% | 17.1% | 19.0% | 9.5% |
| Raster2Seq base | 1 | 67.2 | 68.9 | 65.5 | 16.2% | 26.1% | 17.9% | 13.2% | 28.6% | 4.8% |
| Raster2Seq base | 2 | 71.0 | 70.0 | 72.0 | 22.3% | 33.1% | 29.8% | 19.7% | 38.1% | 4.8% |
| Raster2Seq base | 4 | 71.8 | 70.2 | 73.5 | 23.1% | 34.2% | 29.8% | 19.7% | 40.5% | 4.8% |
| Classical | 0 | 38.9 | 37.4 | 40.6 | 0.4% | 2.1% | 1.2% | 0.0% | 0.0% | 0.0% |
| Classical | 1 | 45.8 | 42.2 | 50.0 | 12.5% | 16.3% | 13.1% | 0.0% | 0.0% | 0.0% |
| Classical | 2 | 48.9 | 44.2 | 54.6 | 14.8% | 20.0% | 16.7% | 0.0% | 0.0% | 0.0% |
| Classical | 4 | 49.5 | 44.7 | 55.6 | 15.2% | 20.7% | 16.7% | 0.0% | 0.0% | 0.0% |

**Caution:** these numbers include C5, which reads printed "NN.NN m²" text. Where a room prints its area, that text equals Junction's answer for that room, so part of the question gains may come from reading the answer rather than from better segmentation. The ablation without C5 is in the next section.

Edits applied over all iterations:

| Starting tool | Edits by rule | Seconds per sheet |
| --- | --- | ---: |
| EoMT-L fine-tuned | C5-snap 33, C7-add 25, C5-other 21, C1-add 18, C4-split 13, C4-grow 1 | 8.0 |
| RF-DETR-Seg-L fine-tuned | C5-other 57, C1-add 49, C5-snap 33, C7-add 31, C4-split 22, C3-snap 3, C4-grow 3 | 10.7 |
| Raster2Seq base | C1-add 278, C5-other 200, C4-split 117, C3-snap 105, C7-add 100, C5-snap 46, C2-split 16, C4-grow 11 | 49.4 |
| Classical | C7-add 223, C5-other 128, C5-snap 17, C4-split 7, C4-grow 7, C3-snap 4 | 49.1 |

### 4.5 Phase 2 ablation: arm D without printed-area text (no C5)

Each cell: n = 0 → n = 4 with C5 / **n = 4 without C5**. Junction questions are % within 2%.

| Starting tool | Room F1 | Rooms within 2% | Q room | Q apartment | Q label | Q floor |
| --- | --- | --- | --- | --- | --- | --- |
| EoMT-L fine-tuned | 96.4 → 96.6 / **96.6** | 65.3% → 68.4% / **65.3%** | 76.2 → 76.2 / **76.2** | 84.2 → 89.5 / **88.2** | 76.2 → 90.5 / **90.5** | 64.3 → 73.8 / **73.8** |
| RF-DETR-Seg-L fine-tuned | 94.5 → 94.0 / **94.0** | 55.9% → 61.6% / **56.8%** | 67.9 → 71.4 / **66.7** | 69.7 → 77.6 / **73.7** | 81.0 → 88.1 / **85.7** | 59.5 → 50.0 / **50.0** |
| Raster2Seq base | 62.2 → 71.8 / **71.2** | 7.6% → 23.1% / **9.9%** | 8.3 → 29.8 / **9.5** | 17.1 → 19.7 / **11.8** | 19.0 → 40.5 / **23.8** | 9.5 → 4.8 / **2.4** |
| Classical | 38.9 → 49.5 / **49.4** | 0.4% → 15.2% / **5.9%** | 1.2 → 16.7 / **7.1** | 0.0 → 0.0 / **0.0** | 0.0 → 0.0 / **0.0** | 0.0 → 0.0 / **0.0** |

**What the loop really adds without DeepSeek:**
- **Finding missed rooms is real and does not depend on printed areas.** Room F1 and recall gains survive without C5: Raster2Seq 62 → 71, classical 39 → 49, EoMT recall 95.7 → 97.9%. So do EoMT's gains on Junction totals: floor 64 → 74%, label 76 → 90%, apartment 84 → 88%. These come from the room-number checks (C4), the other tool's rooms (C7) and corroborated empty regions (C1).
- **Fixing room areas does depend on printed areas.** Without C5, rooms within 2% barely move: EoMT 65.3 → 65.3%, Raster2Seq 7.6 → 9.9%. With C5 they rose to 68.4% and 23.1%. That gain only exists where a room prints its area, and there the printed value is already Junction's answer. So it is answer-reading, not better segmentation, and it is excluded from the headline.
- **Adding rooms has a precision cost.** RF-DETR-Seg-L's floor totals fall (60 → 50%) and Raster2Seq's apartment totals fall (17 → 12%): extra or wrongly bounded rooms inflate sums. This is where a judge (DeepSeek, arm B) should help, by rejecting doubtful additions.
- **Converges fast.** Nearly all gains arrive at n = 1–2, and n = 3–4 add almost nothing (PLAN §7). Proposed fixed cap: n = 2.
- **Cost:** 8–11 s per sheet for the strong tools and about 50 s for the weak ones (many more edits), on CPU.


### 4.6 Phase 2 on Swiss test (50 sheets, run once): arm D frozen at no C5, n_max = 2

Questions per family on test: room 100, apartment 90, label 48, floor 49. One question is 1–2 points, so differences of a few points are within noise.

| Starting tool | n | Room F1 | Precision | Recall | Rooms within 2% | Rooms within 5% | Q room | Q apartment | Q label | Q floor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EoMT-L fine-tuned | 0 | 95.9 | 97.3 | 94.5 | 62.7% | 82.4% | 66.0% | 87.8% | 89.6% | 57.1% |
| EoMT-L fine-tuned | 2 | 96.1 | 94.8 | 97.4 | 63.1% | 83.2% | 65.0% | 83.3% | 97.9% | 71.4% |
| RF-DETR-Seg-L fine-tuned | 0 | 93.4 | 95.4 | 91.5 | 50.9% | 72.6% | 63.0% | 64.4% | 72.9% | 46.9% |
| RF-DETR-Seg-L fine-tuned | 2 | 93.8 | 91.6 | 96.1 | 52.2% | 75.2% | 64.0% | 67.8% | 81.2% | 55.1% |
| Raster2Seq base | 0 | 62.5 | 78.1 | 52.1 | 10.4% | 22.2% | 10.0% | 20.0% | 29.2% | 16.3% |
| Raster2Seq base | 2 | 71.2 | 69.9 | 72.5 | 13.4% | 27.2% | 17.0% | 24.4% | 31.2% | 12.2% |
| Classical | 0 | 41.9 | 40.5 | 43.3 | 0.7% | 2.8% | 0.0% | 0.0% | 0.0% | 0.0% |
| Classical | 2 | 55.8 | 49.7 | 63.5 | 7.3% | 15.5% | 8.0% | 0.0% | 0.0% | 0.0% |

- **Confirms validation.** Repairs raise recall for every starting tool: EoMT 94.5 → 97.4%, RF-DETR 91.5 → 96.1%, Raster2Seq 52 → 73%, classical 43 → 64%. Precision falls by 3–9 points.
- **Totals improve where single missed rooms were the problem.** EoMT floor questions go from 57.1% to 71.4% (+7 of 49), label from 89.6% to 97.9%. RF-DETR floor goes from 46.9% to 55.1%, label from 72.9% to 81.2%.
- **But not everywhere.** EoMT apartment questions fall from 87.8% to 83.3% (−4 of 90), and Raster2Seq floor from 16.3% to 12.2%. An added or split room lands in the wrong apartment, or an extra room inflates a sum. These are the cases a judge (DeepSeek, arm B) is meant to catch.
- **Small area errors are unchanged**, as phase 1 predicted. Rooms within 2%: EoMT 62.7 → 63.1%.
- **Best Swiss pipeline so far:** fine-tuned EoMT-L + heal + arm D (n = 2). Room F1 96.1; Junction floor 71%, label 98%, apartment 83%, room 65%. The semseg trial's EoMT row was floor 59%, label 90%, apartment 89%, room 65%.

### 4.7 CubiCasa: comparator (phase 1) and arm D (phase 2)

CubiCasa prints no room numbers (C4 inactive) and has no scale (C5 inactive), so the loop relies on C1 (corroborated by OCR words or the other tool), C2, C3 and C7. Area here is relative pixel area against the human annotation (semseg RESULTS §5).

**Comparator on validation (77 drawings):**

| Starting tool | Flags | Correct flags | Errors | Errors found | C7: correct / found | PSD (ablation): correct / found |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| EoMT-L fine-tuned | 672 | 71.0% | 754 | 46.4% | 93.6% / 32.8% | 61.3% / 29.2% |
| RF-DETR-Seg-L fine-tuned | 574 | 66.2% | 703 | 36.7% | 85.9% / 23.2% | 54.8% / 31.7% |
| Raster2Seq base | 792 | 81.1% | 1041 | 56.0% | 96.3% / 32.2% | 77.4% / 33.1% |
| Classical | 702 | 97.7% | 1809 | 66.1% | 97.5% / 64.5% | 89.4% / 30.5% |

Unlike on Swiss, the PSD check is fairly precise on CubiCasa (55–89%). Real scans have texture and noise that a room rendering lacks, so tile spectra differ where rooms are missing. It is still kept out of the loop, because it is less precise than C7 on the strong tools.

**Arm D on validation (n up to 4):**

| Starting tool | n | Room F1 | Precision | Recall | Rooms within 2% | Rooms within 5% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| EoMT-L fine-tuned | 0 | 80.9 | 89.6 | 73.8 | 31.3% | 49.8% |
| EoMT-L fine-tuned | 1 | 82.1 | 81.0 | 83.1 | 33.8% | 54.8% |
| EoMT-L fine-tuned | 2 | 81.5 | 80.1 | 83.0 | 33.8% | 54.8% |
| EoMT-L fine-tuned | 4 | 81.5 | 80.1 | 83.0 | 33.8% | 54.8% |
| RF-DETR-Seg-L fine-tuned | 0 | 84.2 | 89.5 | 79.4 | 37.3% | 58.1% |
| RF-DETR-Seg-L fine-tuned | 1 | 80.6 | 78.3 | 83.1 | 37.4% | 58.4% |
| RF-DETR-Seg-L fine-tuned | 2 | 80.5 | 78.1 | 83.0 | 37.4% | 58.2% |
| RF-DETR-Seg-L fine-tuned | 4 | 79.9 | 77.1 | 83.0 | 37.4% | 58.1% |
| Raster2Seq base | 0 | 72.5 | 75.8 | 69.4 | 15.6% | 31.6% |
| Raster2Seq base | 1 | 70.7 | 69.5 | 72.0 | 16.1% | 32.3% |
| Raster2Seq base | 2 | 70.5 | 69.2 | 72.0 | 16.1% | 32.3% |
| Raster2Seq base | 4 | 70.5 | 69.1 | 72.0 | 16.1% | 32.3% |
| Classical | 0 | 22.2 | 21.3 | 23.2 | 0.6% | 1.5% |
| Classical | 1 | 42.8 | 36.5 | 51.8 | 10.4% | 18.6% |
| Classical | 2 | 42.8 | 36.5 | 51.8 | 10.4% | 18.6% |
| Classical | 4 | 42.8 | 36.5 | 51.8 | 10.4% | 18.6% |

**Arm D on test (485 drawings, run once, n_max = 2):**

| Starting tool | n | Room F1 | Precision | Recall | Rooms within 2% | Rooms within 5% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| EoMT-L fine-tuned | 0 | 79.9 | 87.5 | 73.5 | 29.7% | 49.3% |
| EoMT-L fine-tuned | 1 | 80.1 | 77.8 | 82.6 | 31.6% | 52.9% |
| EoMT-L fine-tuned | 2 | 80.1 | 77.8 | 82.6 | 31.6% | 52.8% |
| RF-DETR-Seg-L fine-tuned | 0 | 83.8 | 89.3 | 78.9 | 38.1% | 58.4% |
| RF-DETR-Seg-L fine-tuned | 1 | 81.2 | 79.5 | 83.1 | 38.4% | 59.1% |
| RF-DETR-Seg-L fine-tuned | 2 | 81.3 | 79.4 | 83.3 | 38.5% | 59.3% |
| Raster2Seq base | 0 | 73.6 | 74.1 | 73.1 | 17.5% | 34.2% |
| Raster2Seq base | 1 | 73.1 | 71.2 | 75.1 | 18.0% | 34.8% |
| Raster2Seq base | 2 | 73.0 | 71.0 | 75.1 | 18.1% | 34.8% |
| Classical | 0 | 24.1 | 22.1 | 26.5 | 0.9% | 2.7% |
| Classical | 1 | 42.0 | 34.9 | 52.6 | 8.8% | 17.8% |
| Classical | 2 | 42.0 | 34.9 | 52.6 | 8.8% | 17.7% |

- **On real scans, the loop without a judge mostly trades precision for recall.**
  - Recall rises for every tool (EoMT 73.5 → 82.6%, RF-DETR 78.9 → 83.3%).
  - Precision falls by 9–10 points for the two strong tools.
  - Room F1 is flat for EoMT (79.9 → 80.1) and **falls for RF-DETR** (83.8 → 81.3).
- **Rooms within 5% improve slightly:** EoMT 49.3 → 52.8%, RF-DETR 58.4 → 59.3%. The newly found rooms are mostly the right size.
- **The loop can make real accuracy worse while its own score improves.** RF-DETR's validation F1 keeps falling at n = 3, though each edit lowered the disagreement score. The rules accept rooms that look supported (C1 / C7) but are not in the annotation: CubiCasa "undefined" spaces, closets, outdoor areas. This is the clearest case for a judge (DeepSeek, arm B), or for stricter C1/C7 acceptance tuned on validation.
- **The classical starting point nearly doubles** (Room F1 24 → 42), almost entirely by adopting EoMT's rooms through C7.
- **Iterations:** everything happens at n = 1; n = 2 changes nothing.

### 4.8 Push to 80%: Swiss validation, first attempts

**Where the remaining area error comes from** (fine-tuned EoMT-L + snap, 1,457 validation rooms; 66.7% within 2%):

| Rooms outside 2% | Count |
| --- | ---: |
| 2–5% off | 247 |
| 5–10% off | 102 |
| 10–50% off | 60 |
| More than 50% off (merged or partial) | 37 |
| No room at the label point | 39 |

- **Mostly small errors:** about 72% of failing rooms are only 2–10% off.
- **Snap overshoots:** among 2–10% errors after snap, 260 rooms are too large and 89 too small.
- **Small rooms suffer most:** rooms under about 3 m² are within 2% only 34% of the time.

Looking at the too-large rooms: snap floods into door and window openings, where the reference cuts straight across along the wall face, and it absorbs small shafts.

**Tried; did not help or helped little (validation, rooms within 2%):**

| Change | Rooms within 2% | Note |
| --- | ---: | --- |
| Snap as in the semseg trials (baseline) | 66.6% | |
| Remove protrusions thinner than 0.4 m after snap (mitred opening) | 68.4% | +1.8; door bumps are a minor part |
| Bridge wall gaps up to 1.2 m before snapping, so the flood stops at the wall face | 63.6% | **Worse.** The line closing also seals real openings between rooms |
| Fill only holes under 0.3 m² (keep shafts as holes) | 63.6% | No effect on top of bridging |
| Bridging + opening; different snap offsets (0.5 / 1.5 px) | 48.9–64.8% | Worse |

Conclusion: geometric post-processing of the current masks is close to its limit (about 66–68%). The masks themselves need to be sharper.


### 4.9 Push to 80%: zoomed re-segmentation (Swiss validation)

Fine-tuned EoMT-L is re-run on a window around each room at about 2× the whole-plan zoom. The room's outline is replaced with the best-overlapping instance from that window (IoU ≥ 0.5). Mask cells shrink from about 7 to about 3.5 page pixels, while rooms stay close to the size the model was trained on. Cost: 12.6 s per sheet on the GPU.

| Pipeline (validation) | Room F1 | Rooms within 2% | Rooms within 5% | Median error | Q room | Q apartment | Q label | Q floor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| EoMT-L | 94.6 | 64.6% | 83.9% | 1.2% | 77.4% | 82.9% | 76.2% | 66.7% |
| EoMT-L + zoom | 94.9 | **80.9%** | **91.9%** | **0.6%** | **91.7%** | 82.9% | 78.6% | 59.5% |
| EoMT-L + zoom + heal + arm D (n = 1; n = 2–4 identical) | 96.2 | **80.9%** | **91.8%** | **0.6%** | **90.5%** | **82.9%** | **88.1%** | 61.9% |

- **Zoom is the lever that moves area accuracy.** Rooms within 2% rise from 65% to 81%, and Junction room questions from 77% to 92%. Post-processing could not get past 68%. The resolution of the masks was the bottleneck.
- **Four of the six 80% targets are now met on validation:** rooms within 2%, rooms within 5%, Q room and Q apartment (82.9%). Q label (88.1%) also meets it, thanks to the repair loop (+9.5 points). **Q floor (61.9%) does not.**
- **Why floors fail** (16 of 42 validation floors):
  - **10 floors:** 1–3 rooms are missing, usually small ones.
  - **6 floors:** every room was found but the total is still 2–9% too low.

  In all 16 the predicted total is too low. The median room is only 0.18% too small and holes are negligible, so the undershoot comes from a few rooms losing big pieces.
- **Tried and rejected:** a uniform outward offset. +0.5 px raises Q floor to 76% and Q apartment to 90%, but drops rooms within 2% to 70% and Q room to 87%: a trade, not a fix.

### 4.10 Push to 80%: closing the floor-total gap (Swiss validation)

Each step builds on the one above: EoMT-L → zoom → heal → arm D (no C5, n = 2).

| Step | Recall | Rooms within 2% | Rooms within 5% | Q room | Q apartment | Q label | Q floor |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Zoom (IoU ≥ 0.5) + arm D | 96.3% | 80.9% | 91.8% | 90.5% | 82.9% | 88.1% | 61.9% |
| Zoom replaces an outline only at IoU ≥ 0.8 (stops partial instances replacing whole rooms) | 96.2% | 80.3% | 90.7% | 89.3% | 81.6% | 88.1% | 69.0% |
| + number-anchored grow (C4): bridging radius stepped down until the region holds this number and no other | 96.3% | 80.3% | 90.7% | 89.3% | 82.9% | 90.5% | 71.4% |
| + OCR text boxes erased from the ink before growing (text is not wall) | **97.3%** | 80.3% | 90.7% | 89.3% | **86.8%** | **95.2%** | **73.8%** |
| + partial-room check C11 / extend repair | 97.3% | 80.3% | 90.7% | 89.3% | 86.8% | 95.2% | 73.8% (fired once) |

- **Why the grow failed before:** 26 of 37 missed indoor rooms had their number read by OCR. Growing from it failed because the old rule bridged doors with a 20 px ink dilation, which erases rooms under about 2 m², and the number's own text covered the seed.
- **Remaining floor failures** (11 of 42):
  - **7 floors:** one large room is 8–31 m² too small (a corridor or living area only partly covered).
  - **4 floors:** a room is still missing (stairs and lifts, a group of storerooms).

  The partial-room check rarely fires, because these rooms open onto neighbours through openings wider than any bridging radius.
- **Frozen for test** (validation only was used to choose it):
  1. EoMT-L
  2. zoom, ×2, replace only at IoU ≥ 0.8
  3. heal
  4. arm D: no C5, n = 2, C4 number-anchored grow, C11

  **Swiss test: done** (next section).

### 4.11 Control: DeepSeek answers directly from the PNG

`src/stoa_iterative/vlm_control.py`, your requested control.
- **Input:** one call per question, with the sheet PNG as Junction ships it (150 DPI) and the English question. No tools, no structured data, temperature 0.
- **Output:** JSON `{status, value, unit}`, scored with Junction's 2% rule.
- **Questions:** the same Swiss test questions as the pipeline, 287 in all (room 100, apartment 90, label 48, floor 49).
- **Printed answers reported separately:** 22 of the 100 room questions have the room's area printed on the sheet, so the VLM can read the answer.
- **Safeguards:** every response is cached; failures are logged, never retried automatically; the run stops before exceeding the spending cap.

Datasets:
- **Swiss:** yes.
- **CubiCasa:** has no area questions (counts only), so no area control is possible there.
- **IFC plan set:** would be a third dataset once prepared.

**Now run on Verda** (`deepseek-4-1-flash-1017`), and extended to every count question type, for Swiss and CubiCasa.

**Swiss test, strict prompt (Junction's "Do not guess"), 923 questions, US$0.94:**

| Question type | n | DeepSeek alone | Answered at all | Pipeline (arm D, final) |
| --- | ---: | ---: | ---: | ---: |
| Room area | 100 | 20.0% | 32% | 90.0% |
| — room area printed on the sheet | 22 | 86.4% | | |
| — room area not printed | 78 | 1.3% | | |
| Apartment area | 90 | 0.0% | 22% | 86.7% |
| Label area | 48 | 2.1% | 31% | 97.9% |
| Floor area | 49 | 2.0% | 16% | 77.6% |
| **All area** | 287 | **7.7%** | | **88.2%** |
| Count: rooms labelled X | 324 | 52.8% | 100% | 82.7% |
| Count: apartments | 50 | 40.0% | 100% | 92.0% |
| Count: doors | 50 | 0.0% | 98% | not answerable |
| Count: windows | 50 | 8.0% | 94% | not answerable |
| Count: toilets | 50 | 40.0% | 100% | not answerable |
| Count: washbasins | 50 | 26.0% | 98% | not answerable |
| Count: bathtubs | 44 | 22.7% | 98% | not answerable |
| Count: showers | 18 | 16.7% | 100% | not answerable |
| **All count** | 636 | **37.9%** | | |

- **DeepSeek alone cannot measure area from the PNG.** Where it gets a room area right, it read the printed number: 86% where printed, 1% elsewhere. Without a printed number it mostly abstains.
- **The structure wins every type both can answer:** area 88% vs 8%, label counts 83% vs 53%, apartment counts 92% vs 40%.
- **DeepSeek alone is the only answer for doors, windows and fixtures** (0–40%), which the room graph cannot answer yet.

### 4.12 Swiss test (50 sheets, run once): every metric, by task type (arm D)

Junction scores **area** questions within 2% and **count** questions by exact match. "—" means the pipeline has no way to answer that type yet: doors, windows and fixtures need the openings and fixtures layer of L1 (PLAN §5.4), and rooms within 2% / 5% need room outlines. The control row needs the endpoint.

| Pipeline | Rooms within 2% | Rooms within 5% | Q room | Q apartment | Q label | Q floor | Q area, all | Q count: rooms labelled X | Q count: apartments | Q count: doors | Q count: windows | Q count: toilets | Q count: washbasins | Q count: bathtubs | Q count: showers | Q count, answerable families |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| *questions on test* | — | — | 100 | 90 | 48 | 49 | 287 | 324 | 50 | 50 | 50 | 50 | 50 | 44 | 18 | 374 |
| EoMT-L + heal (n = 0) | 62.7% | 82.4% | 66.0% | 87.8% | 89.6% | 57.1% | 75.3% | 78.7% | 92.0% | — | — | — | — | — | — | 80.5% |
| EoMT-L + heal + arm D, first rules (n = 2) | 63.1% | 83.2% | 65.0% | 83.3% | 97.9% | 71.4% | 77.4% | 80.6% | 92.0% | — | — | — | — | — | — | 82.1% |
| EoMT-L + zoom + heal (n = 0) | 79.8% | 89.3% | 91.0% | 85.6% | 91.7% | 55.1% | 83.3% | 78.7% | 92.0% | — | — | — | — | — | — | 80.5% |
| EoMT-L + zoom + heal + arm D, final (n = 2) | 80.1% | 90.3% | 90.0% | 86.7% | 97.9% | 77.6% | 88.2% | 82.7% | 92.0% | — | — | — | — | — | — | 83.9% |
| OCR text only (no rooms; counts only) | — | — | — | — | — | — | — | 83.3% | 92.0% | — | — | — | — | — | — | 84.5% |
| **DeepSeek V4.1 Flash, PNG + question → answer (control)** | — | — | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending |

- **Final pipeline** (EoMT-L + zoom + heal + arm D, n = 2) **against your 80% target:**
  - **Met:** rooms within 2% 80.1%, within 5% 90.3%, Q room 90.0%, Q apartment 86.7%, Q label 97.9%, all area questions pooled 88.2%, apartment count 92.0%, rooms-labelled-X count 82.7%.
  - **Just short:** Q floor 77.6%.
  - **Not answerable yet:** doors, windows and fixture counts. The room graph has no openings or fixtures; the counting-trial detectors could fill that layer.
- **What each stage adds (Q area, all):**
  - Repair loop alone: 75.3% → 77.4%.
  - Zoom alone: 75.3% → 83.3%.
  - Both together: **88.2%**.
  - Floor totals: 57.1% → 77.6%, mostly from the loop's recall repairs on top of zoom.
- **Counting rooms by label needs no rooms.** Counting name tokens in the OCR scores 83.3%, about the same as counting named rooms in the graph (82.7%). For that family the room structure adds nothing over the text. Apartment counts (92.0%) come from OCR labels in both rows.


### 4.13 Phase 3: DeepSeek arms B, C, E (Verda), n = 0–4

**What each arm does (PLAN §6):**
- **B, DeepSeek with tools.**
  - **Input:** the sheet with the current rooms drawn on it, up to 6 flagged regions, and a crop of each.
  - **Tools** (native function calling): `view_region`, `segment_region`, `grow_room_from_seed`, `snap_room`, `split_room_by_walls`, `rule_repair`, `read_text`, `room_stats`.
  - **Output:** `submit_edits` with typed operations (add, replace, delete, merge, split, apply rule) and dismissals of false-alarm flags.
  - **Limits:** at most 3 calls per sheet per iteration, at most 2 tool calls per response.
- **C, DeepSeek with prompted JSON.** Same information, but no tool choice: the host runs every tool on every flag first and draws the candidates. DeepSeek returns one JSON list of the same operations, in one call. This tests whether the model choosing its tools matters.
- **E, DeepSeek with oracle flags.** As B, but the flags are the true errors from the reference: a missed room, a false room, or an area more than 2% off. It is the ceiling for the repair step. Acceptance counts only fewer true errors.
- **All arms:**
  - DeepSeek never writes coordinates; it picks among tool candidates.
  - Every operation is checked before it is applied: valid polygon, minimum size, inside the plan, no printed room number left uncovered.
  - An iteration that does not lower the disagreement score is reverted, and that sheet stops.

**Validation, n = 4 (n = 0 is the same for every arm):**

| Dataset, starting method | Arm | Room F1 | Precision | Rooms within 2% | Q area, all | Q floor | Q count, answerable | US$ |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Swiss, EoMT-L + zoom | n = 0 | 96.3 | 96.9 | 80.7 | 81.6 | 66.7 | 78.3 | |
| | D | 96.5 | 95.7 | 80.3 | 86.9 | 73.8 | 80.9 | 0 |
| | **B** | 96.7 | 97.0 | 80.7 | **87.3** | **76.2** | 80.9 | 0.05 |
| | C | **96.9** | 96.4 | 80.7 | **87.3** | 73.8 | **81.2** | 0.02 |
| | E | 96.6 | **97.4** | **83.0** | 83.6 | 69.0 | 78.3 | 0.06 |
| Swiss, Raster2Seq | n = 0 | 62.2 | 78.6 | 7.6 | 13.1 | 9.5 | 55.0 | |
| | D | **70.6** | 69.1 | 9.6 | 11.5 | 2.4 | **65.0** | 0 |
| | **B** | 66.4 | **79.5** | 9.8 | **14.8** | **11.9** | 63.1 | 0.20 |
| | C | 69.8 | 77.7 | **10.6** | 13.5 | 4.8 | **65.7** | 0.11 |
| | E | 62.4 | 79.0 | 8.4 | 14.3 | 9.5 | 55.0 | 0.07 |
| CubiCasa, RF-DETR-Seg-L | n = 0 | 84.2 | 89.5 | 37.3 | — | — | 74.1 | |
| | D | 79.9 | 77.1 | 37.4 | — | — | 73.7 | 0 |
| | **B** | 84.3 | 88.2 | 37.5 | — | — | 74.1 | 0.13 |
| | C | 82.1 | 82.1 | 37.4 | — | — | 73.7 | 0.06 |
| | E | **85.4** | **91.4** | **39.0** | — | — | **75.0** | 0.12 |

- **On real scans the judge works.** Starting from RF-DETR on CubiCasa, arm D adds rooms that are not in the annotation (F1 84.2 → 79.9, precision 89.5 → 77.1). B mostly dismisses those flags (244 dismissals, 19 edits at n = 0) and holds F1 at 84.3.
- **Tool choice matters.** C, which is shown every candidate, accepts more of them and loses part of B's gain on CubiCasa (82.1).
- **On Swiss, B and C edge past the rules** from the frozen pipeline: Q floor 76.2 for B against 73.8 for D. From Raster2Seq, the rules recover more rooms, but B's choices give better answers (Q area 14.8 vs 11.5, Q floor 11.9 vs 2.4).
- **E is a low ceiling.** Even told exactly where the errors are, DeepSeek fixes few: Raster2Seq F1 only goes from 62.2 to 62.4. The tools cannot produce the right polygon for most errors, so the bottleneck is the repair tools, not the judgement.
- **Iterations:** the DeepSeek arms keep improving to n = 3–4 from Raster2Seq, where D stops at n = 2.
- **Gate (PLAN §11, phase 3): met.** B beats D on validation for at least one generator. Test runs for B, C and E (Swiss, EoMT-L + zoom) and B (CubiCasa, RF-DETR) are queued after the control.
- **Spend so far:** about US$1.9 of the US$15 cap.
