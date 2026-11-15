# Native geometry implementation status

The approved geometry plan is in progress. This document describes implemented
behavior, not acceptance of the complete plan.

## Available in this change

DXF uploads use pinned `ezdxf==1.4.3`. Import selection lists supported model-space
entities by handle and layer. Selected closed polylines, circles, ellipses and
splines are extracted; INSERT blocks are decomposed with source transforms.
Off/frozen layers, hidden entities, open boundaries and non-horizontal geometry
are rejected. The generated preview shows selected footprints only; it cannot
establish object counts or complete building coverage.

Declared INSUNITS supply source scale. Unitless files retain an unavailable
physical area until the user confirms units in the import dialog. Conflicting
confirmations are rejected. Metres, millimetres and feet have analytic coverage.
Curves are flattened with a 1 mm chord tolerance after unit conversion (0.001
source units when scale is absent). Their calculated areas retain estimate and
approximation metadata. Separate selected nested boundaries are not interpreted
as holes; overlapping selections require review.

PDF pages expose `read_pdf_vectors`: paginated original line, rectangle, quad
and cubic-curve commands transformed to registered full-page pixels. Curves
retain their control points. Rotated and cropped pages have deterministic tests.
This inventory preserves the page image, object detection and raster fallback;
it does not certify visibility, infer room boundaries or establish physical scale.

Area and distance results include source fields, method, unit, calibration hash,
and content-derived measurement IDs. Area IDs also bind the polygon geometry.
New final answers select tool calculations through `measurement_ids`; unselected
exploratory calculations stay in the tool journal. Room selections still compute
their own source-bound area. Historical stored answers are not rewritten.

## Reproduction

Use Python 3.12 or newer, repository dependencies, and the maintained runtime on
PYTHONPATH. The targeted deterministic suite is:

```
PYTHONPATH=vendor/stoa-runtime/src python -m pytest -q tests/test_dxf_import.py tests/test_measurement_receipts.py tests/test_lens_integration.py tests/test_pdf_vectors.py
```

For browser validation, generate a DXF containing a selected closed 10 by 5 metre
polyline at `/tmp/ymir-test.dxf`, run the local API and frontend, and run:

```
YMIR_TEST_FRONTEND=http://127.0.0.1:3021 node scripts/check_dxf_import_browser.cjs
```

`YMIR_DXF_FIXTURE` overrides the fixture path. This script checks selection,
confirmed units in preparation, saved native sheets, and source format after
reload. It makes no model calls. The job snapshot test uses an injected runner
and actual registered extraction, host geometry, answer and evidence polygons.
It does not measure live model selection accuracy.

## Still required for plan acceptance

PDF boundary association and measurement integration, SVG curve and drawing expansion,
IFC wall/slab inventories and solids, wall reconstruction and centreline totals,
volumes and dimension-entry requests, extraction caching, Swiss PDF-only and
DXF-only tolerance validation, and complete multi-format browser acceptance
remain outstanding. No complete-plan acceptance or deployment is claimed.
