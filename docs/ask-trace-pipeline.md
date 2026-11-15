# Ask pipeline and trace receipts

Verified against the checkout on 15 November 2026. The trace displays recorded
execution facts; preview nodes describe pending work. No vendored code is changed.

1. **Question.** `POST /api/ask` in `backend/ymir/app.py` checks that the active
   drawing is selected, the question is nonempty, and the selected drawings exist.
   `JobStore.submit` in `backend/ymir/lens_jobs.py` enforces one to three unique
   sheets and request ID idempotency. A repeated request returns the same receipt.
2. **Queue and snapshot.** Submit copies source images, PDFs/originals and native
   geometry assets as applicable, hashes the snapshot files, registers pages,
   validates prior job ownership, and writes the manifest. Up to six completed
   turns in the same project/conversation provide interpretation context, not new
   evidence. SQLite queues the job. A process lock permits one inference worker;
   the worker checks snapshot hashes again before dispatch.
3. **Route.** `bind_count_scope` in `backend/ymir/lens_intent.py` conservatively
   matches exact one-page whole-sheet count requests, including the explicit
   bathtub/shower group. Native geometry sources do not qualify. A match selects
   `compact_counter` with a bound scope and routed detector; otherwise service
   defaults to `general`. Ymir never selects the runtime's `counter`, `rooms`, or
   explicit `argon_atomic` profiles. Smart/Frontier is a separate model choice,
   snapshotted in the job configuration and preserved through terminal events.
4. **Model work.** `stoa_lens.service.run` registers the immutable source, sets up
   metered transport and detector/geometry resources, and records its implementation,
   source, limit and model manifest. It invokes `BoundCounter` for the bound scope,
   otherwise `LensHarness`. The loop in `harness.py` emits response started/completed
   or failed, and tool started/completed receipts in serial execution order.
   `BoundCounter._emit` enriches detection receipts with its bound classes, detector
   routing and model identity; it does not add another hidden operation.

   Smart uses `deepseek/deepseek-v4.1-flash-1017`; Frontier uses
   `openai/gpt-6.1-sol`, both via pinned OpenRouter endpoints. General tools are VISTA `inspect`
   and `read_pixels`, `detect_objects`, native `read_pdf_text` and `inspect_pdf_vectors`,
   `native_room_candidates`, Raster2Seq `predict_room_polygons`, `calibrate`,
   `measure_room` and `measure_distance`. Detection uses D-FINE-L
   (`d-fine-l-junction-ema`) for trained classes or Argon (`google/gemini-4-argon`) otherwise;
   these are class routes, not automatic fallback attempts. The compact counter
   exposes only inspection, pixels, detection and native PDF text/vector inspection. It has no room
   geometry tools. Tools appear only when their receipts exist.
5. **Host checks.** Binding validates registered proposal IDs, clipping/edge contact,
   duplicates, complete detector evidence and count scope. General binding also
   validates room source/page/polygon, text associations, calibration and reading
   evidence, deriving counts and measurements from host-owned evidence. Validation
   errors can trigger a corrective final response within the existing loop; final
   salvage sets `needs_review`. This is validation inside the harness, not another
   inference agent. The stage receipt records the returned accepted proposals,
   derived count values, validation errors, room/measurement totals and evidence
   status. If the runner raises before returning, there is no checks receipt.
6. **Answer and optional review.** `answer_for` in `backend/ymir/lens_answers.py`
   constructs source-bound citations, count/room/measurement presentation and
   per-sheet/class box review groups. `_terminal` commits job status, the checks
   stage when available, and the answer in one SQLite transaction. The answer is
   the final durable event. In `frontend/lib/workspace.ts`, `ask` starts the request;
   `handleEvent` records receipts and deduplicates sequence numbers. A `needs_review`
   answer with boxes starts `startReview`; a user can also request a group manually.
   `doneReview` saves labels and appends local review statuses; `skipReview` marks
   the review skipped. These local nodes follow the machine answer, with no retired
   verifier parent.

## Difference from the supplied snapshot

The six-stage outline and two Ymir routes still hold. Additional detail in this
checkout: general work can request **native room candidates, PDF vectors and source-bound
calibration**, and the compact route supports an explicit **grouped bathtub/shower
count**. PDF text is native extraction with no dedicated OCR. The updated backend uses **Smart/DeepSeek or Frontier/Sol via OpenRouter**,
with **Argon** for classes outside D-FINE’s trained heads. Detectors are selected
by class; a failed detector is not retried on another backend. Native geometry
also includes selected DXF geometry. Validation corrections
already happen inside the bounded loop; this change adds no inference or retries.

## Compatibility and verification

Submit appends `stage` receipts for `question`, `queue` and `route` after the first
`job` event. Checks are appended only for a returned runtime result, inside the
terminal transaction before `answer`. The database schema and old receipts are
unchanged. `GET /api/agents` returns the pending pipeline, with optional review.

`frontend/lib/lens-trace.ts` projects stage receipts into the same sequence as model
activity. Receipts without stages gain no synthetic stages. Old static graphs keep
their parent links. Live NDJSON, reconnect using `after`, and artifact events all
read the same append-only log. Local entry persistence retains those events and
frontend-owned review receipts.

Run `python -m pytest -q` in the project virtual environment and `npm run check` in
`frontend`. `tests/trace_browser_backend.py` runs the real service and harness with
injected, deterministic model responses and detector boxes, so browser verification
uses no paid inference. Start it on port 8012 and Next on port 3011, then run
`node scripts/check_ask_pipeline_browser.cjs`. It checks both routes, validation and
box review, earlier-answer replay, artifact replay, and mid-job reconnection, and
saves screenshots under `/tmp/ymir-ask-pipeline`.
