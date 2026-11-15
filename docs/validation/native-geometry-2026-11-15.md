# Native geometry progress validation — 2026-11-15

This validates the implemented increment described in
[implementation status](../native-geometry-progress.md). It does not establish
acceptance of the full approved plan or Swiss dataset accuracy.

Environment: Linux, Python 3.13.5, Node from the local environment, pinned
`ezdxf==1.4.3`, `ifcopenshell==0.9.0.post1`, maintained vendored runtime.

| Check | Command | Result |
| --- | --- | --- |
| Backend | `PYTHONPATH=vendor/stoa-runtime/src python -m pytest -q` | 164 passed; upstream deprecation warnings |
| Frontend | `cd frontend && npm run check` | lint/typecheck passed; 47 tests passed |
| Production compilation | `cd frontend && npm run build` | passed |
| DXF browser import | `node scripts/check_dxf_import_browser.cjs` against local API/frontend | passed; no browser errors |
| Patch whitespace | `git diff --check` | passed |

The browser fixture was a closed 10 by 5 metre LWPOLYLINE. Tests confirmed entity
selection and unit confirmation reach preparation, one native sheet is created,
and reload displays DXF as the original format. Screenshots were inspected.

Analytic tests also cover metre/millimetre/foot conversion, a rotated and
nonuniformly scaled block, unitless and conflicting units, open boundaries,
curved area estimates, source preservation/tampering, document reload, job
snapshots, host-calculated area, answer polygons, PDF rotations and crop,
PDF vector pagination and cubic control points, polygon holes, source-bound
measurement IDs, and rejection of unknown final measurement selections.

All inference in these checks is injected or absent. No paid provider calls,
live model accuracy tests, Swiss reference-based extraction, deployment or
historical experiment modification were performed.
