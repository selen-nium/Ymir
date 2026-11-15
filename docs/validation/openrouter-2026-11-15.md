# OpenRouter migration validation — 2026-11-15

The maintained runtime now resolves Smart to DeepSeek and Frontier to Sol through
OpenRouter. Both use D-FINE for trained classes and atomic Argon crop analysis for
other classes. Production state, the existing ceiling, historical charges and
unresolved holds are preserved. No deployment or paid inference was performed
for this migration's checks. Model-quality evaluation remains separate.

| Check | Result |
| --- | --- |
| Backend suite | 191 passed; seven existing upstream ezdxf deprecation warnings |
| Migration subset | 27 passed with all network access blocked, including Verda |
| Frontend lint/typecheck/tests | passed; 54 tests across 11 files |
| Production frontend build | passed |
| Smart/Frontier browser integration | desktop and 390px mobile; no browser errors |
| Existing Lens browser integration | scope, reload/reconnect, evidence review, saved labels, PDF page selection and mobile render passed |
| Live endpoint metadata | all three pinned standard endpoints passed capability and pricing validation |
| Vendored provenance | all recorded current file hashes match |
| Frozen Frontier study | 241 frozen files match their recorded hashes; no experiment writes |
| Production imports | no Verda transport or `stoa.provider` dependency loaded |
| UI detector and patch whitespace | no findings |

Backend coverage includes both model mappings, production service factories,
general and compact loops, exact trained/untrained routing, Argon coordinates and
proposal validation, streamed tool arguments, opaque reasoning continuation,
SDK retry disabling, provider failures, partial billing, missing/invalid billing
holds, historical ledger rows, the unchanged ceiling, deadline admission,
cooperative cancellation, queued-job snapshots, mode conflicts, legacy replay,
route availability isolation and six-turn conversation context.

The new browser check verifies both native selector options immediately beside
Send, keyboard focus/selection, the Smart default, per-chat preference, suggestion
submission, drawing-scope preservation, locking during active work, reconnect
without resubmission, recorded mode/source labels, Sol/Argon trace identities,
unavailable Frontier with working Smart, mobile bounds and absence of visible
accounting data. Screenshots were inspected at desktop and mobile sizes. A trace
projection fixture also verifies historical spending-limit copy without modifying
the original receipt; historical DeepSeek/Kimi labels remain supported.

Live catalog checks were read-only GETs to OpenRouter's model endpoint catalogs.
They confirmed `particle-ai/us`, `openai` and `google-vertex/global`, image inputs,
required tool/JSON/reasoning parameters and at least 65,536 completion tokens.
Transport dispatch and browser jobs used injected responses, rather than claiming
new model-answer accuracy or spending the production ledger on quality probes.

Validation commands:

```sh
PYTHONPATH=backend:vendor/stoa-runtime/src YMIR_STOA_ROOT=/mnt/berstorage/stoa python -m pytest -q
cd frontend && npm run check && npm run build
# Against tests/browser_backend.py and the local production frontend:
node scripts/check_openrouter_browser.cjs
YMIR_TEST_FRONTEND=http://127.0.0.1:3013 YMIR_TEST_BACKEND=http://127.0.0.1:8014 node scripts/check_lens_browser.cjs
```

The pre-existing geometry commit `47527b1` remains intact. Fetching `origin/main`
found no incoming changes requiring reconciliation. Hosting configuration now
references `ymir-openrouter` and `OPENROUTER_API_KEY`; its catalog refresh uses the
isolated production provider directory. Normal commit/push delivers this change;
no manual deployment was invoked.
