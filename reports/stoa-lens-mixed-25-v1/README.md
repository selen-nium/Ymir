# Stoa Lens: 25 questions, two routes

[Open the report](index.html), [per-case metrics](metrics.csv), [full metrics](metrics.json), or [provenance](provenance.json). For a local interactive gallery, serve the repository with `python -m http.server 8080` and open `/reports/stoa-lens-mixed-25-v1/`.

All 50 trajectories completed. Smart uses DeepSeek high without explicit planning; Frontier uses Sol high with explicit planning. Both use OpenRouter, the same D-FINE/Argon paired box policy, up to 20 VISTA views, 12 responses, a 900-second deadline, and a 65,536-token response allowance. Dedicated OCR is disabled; positioned native PDF text remains available. This research paired-box policy differs from production class-based routing.

| Task | Smart | Frontier |
|---|---:|---:|
| Counting, exact | 5/10 | 7/10 |
| Length, within 5% | 5/5 | 5/5 |
| Volume, within 2% | 5/5 | 5/5 |
| Computed room area, within 5% | 1/3 | 0/3 |
| Printed room area, exact | 2/2 | 2/2 |

Smart answered 22/25 with 3 reviews; Frontier answered 20/25 with 5 reviews. Reviews receive no accuracy credit. Total settled API charges were US$3.425754 across 356 requests, with no unresolved holds. Two visual-phase post-response SQLite errors are preserved in the original study; authoritative generation receipts settled their charges without paid retries. WAL mode and a longer, explicitly closed budget connection were applied prospectively. Forty tool and integration checks passed.

The sample covers CubiCasa, Swiss Dwellings, IFCBench-linked source models, and OpeningDesign. These are exposed diagnostics, not a representative Junction score or an unseen-project test. IFCBench and the Junction IFC Models witnesses have byte-identical source hashes and are not independent projects. References were used only offline.

Lengths measure explicit IFC wall axes; four cases differ from annotation conventions by 2.8–4.6%, despite complete element coverage. Volumes subtract represented openings and reject invalid solids. One case on each route uses complete authored IFC NetVolume quantities because one wall is non-manifold; it is explicitly labeled `source_reported`, distinct from computed geometry. Computed areas and printed schedule readings are scored separately. The weak computed-area performance remains unresolved.

This publication bundles source drawing PNGs and per-case evidence JSON. Local path prefixes are redacted in the portable copies. No provider credentials, ledgers, raw API bodies, original IFC/PDF files or model checkpoints are included. The canonical source/runtime hashes are retained in `provenance.json`; hashes in `selection-sha256.json` refer to the unchanged canonical artifacts, not these redacted copies.

## Computed-area routing limitation

The Swiss native-room study adapter has a source-ID mismatch: the registry uses page IDs while the legacy backend is keyed by Swiss dataset IDs. All six native-room attempts returned unsupported_native_source before extraction. Computed-area scores therefore reflect Raster2Seq fallback, not native extraction performance. Both routes are affected; IFC length/volume uses a separate backend and is unaffected. Frozen runtime, outcomes and scores are unchanged. See [receipt-backed diagnostic](native-room-routing-diagnostic.json).
