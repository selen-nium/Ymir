# Vercel, Modal and OpenRouter

The Next.js frontend runs on Vercel. A single CPU API container on Modal owns
SQLite, the durable inference queue and source artifacts. D-FINE-L and Raster2Seq
run as private Modal GPU functions. DeepSeek, Sol and Argon use pinned OpenRouter endpoints. Browser code
contains only the API origin; provider credentials stay in the backend secret.

## Models

Install `modal==1.6.4` and authenticate with your own Modal profile. Prepare assets
from the existing local asset workspace once:

```sh
python scripts/prepare_modal_assets.py --source ../stoa
python -m modal run deploy/modal_models.py --output var/modal-smoke.json
```

The upload preserves checkpoint hashes, source code and licences. It omits datasets,
reference annotations and credentials. It refuses to overwrite an existing bundle.
The smoke check performs actual batch-one inference on the cached CubiCasa source,
using no annotations. D-FINE loads its pinned epoch-18 EMA strictly and runs BF16 on
an A10G. Raster2Seq uses isolated Python 3.10, Torch 2.3.1/CUDA 11.8 and compiled
CUDA operators; EMA coverage must have no missing or unexpected learned weights.
Both functions have zero automatic retries, one maximum container, 180-second
execution and startup limits, and a two-second idle window. Raster compilation
has a separate 600-second limit. GPU containers scale to zero.

Publish a compact successful receipt with
`python scripts/publish_modal_readiness.py var/modal-smoke.json` before API deployment.
The tested Raster2Seq environment uses Python 3.10; keep Modal pinned to 1.6.4
until its Python environment and custom operators have been upgraded and retested. Model changes require a new smoke receipt. The CPU adapter refuses
an unverified checkpoint and records the model proof in each job manifest.

## API and state

Create the backend-only Modal secret `ymir-openrouter` with `OPENROUTER_API_KEY`. Do not put these in Vercel variables.
Obtain secrets through your existing secret manager or Modal dashboard, never Git.

The existing local US$25 ledger must move with the jobs, uploads and reviews. Finish
or cancel pending jobs and stop the local API before the one-time cutover:

```sh
python scripts/upload_modal_state.py --state var
YMIR_CORS_ORIGINS=https://your-project.vercel.app python -m modal deploy deploy/modal_app.py
```

The importer refuses an active worker or existing remote files. Uploaded document
paths resolve against the current workspace when read, so the preserved drawing
records work after moving to `/data` without rewriting historical JSON. The hosted API
refuses to start without the existing ledger. Do not run local and hosted writers
against divergent copies of that ledger. Historical conversations keep their job
references because the migration retains IDs and artifacts.

The API keeps one CPU container warm so jobs continue after every browser
connection closes. This incurs idle CPU/memory cost; GPU workers remain independent.
A redeploy or crash can briefly run two API containers on the same volume, so only
the holder of the `ymir-writer-lease` Modal Dict entry commits or runs jobs. A new
container serves reads and answers writes with 503 ("finishing an update") until the
previous one releases the lease on shutdown, or its heartbeat lapses for 60 seconds.
It then reloads the volume, marks jobs left running as interrupted, and takes over.
Job streams are async and hold no worker thread, and the container accepts up to
128 concurrent requests, so many viewers can watch jobs at once.
All mutations commit the data volume, including source intake failures. Job status,
progress, reservations and settlements commit at operation boundaries. Interrupted
jobs stay stopped, and unknown billing retains its reservation. Fresh OpenRouter endpoint metadata
is checked before dispatch. The catalogs and pacing database live under `/data/lens/openrouter`
(`YMIR_OPENROUTER_HOME` overrides this). The writer refreshes each model independently at startup;
dispatch refreshes its own model if needed. No OAuth or Verda credentials are used. Readiness
reports Smart and Frontier separately, including their shared Argon and GPU tools. OpenRouter
`usage.cost` settles reservations; missing billing keeps the full hold. Each private GPU call holds $0.25 in the same budget;
actual Modal billing must be reconciled separately. It is never reported as an
LLM token charge or guessed from inference duration.

The API remains reachable without a login, as in the local app. The shared ledger
bounds paid inference. Keep access within the intended workspace until access
controls are configured for a broader launch.

## Frontend

Import this repository into Vercel with **Root Directory `frontend`**. The checked-in
`frontend/vercel.json` selects Next.js and the production build. Set
`NEXT_PUBLIC_YMIR_API_BASE=https://your-workspace--ymir-web.modal.run` at build time.
The build rejects a missing or non-HTTPS production origin. Set the matching
`YMIR_CORS_ORIGINS` on Modal; optionally allow your specific Vercel preview URL
pattern with `YMIR_CORS_ORIGIN_REGEX`.

Uploads, images and reconnectable NDJSON job streams go directly from the browser
to Modal. This avoids passing drawing files through Vercel Functions, whose payload
limit is 4.5 MB ([Vercel limits](https://vercel.com/docs/functions/limitations)).
Local development continues to use Next's `/api` proxy via `YMIR_API_BASE`.
Changing a `NEXT_PUBLIC_` variable requires rebuilding the frontend.

The manual GitHub Actions deployment deploys private GPU functions first, then the
API. It expects the preprovisioned model volume, state volume, smoke receipt and
OpenRouter secret; it never uploads weights or resets the ledger automatically.

## Validation performed

On 15 November 2026, both private functions passed real inference on Modal A10
GPUs and were deployed as `ymir-models`. The pinned detector loaded strictly; the
room model reported full EMA coverage and both compiled operator hashes. The
local adapter called both deployed functions, validated the returned proofs, and
served a repeated room request from its cache. A CPU container also reached both
private functions using Modal's ambient identity, without provider keys in the
browser.

The CPU API image passed its default Ask factory check with an injected language
model, native SVG subprocess extraction, SQLite job deduplication, five durable
checkpoints and an independent container reading seven persisted events. An
uncalibrated SVG correctly remained `needs_review`. The fixture made no paid
language-model calls. Receipt publishing uses an exclusive claim and atomic
rename because Modal volumes do not support hard links.

The production frontend passed a direct cross-origin 6,056,544-byte PNG upload,
source-image loading, NDJSON streaming, reload/reconnect and edited-label
persistence. Failed compiler, Python compatibility and API packaging checks remain
in local `var/` logs; they were corrected before the successful checks. No historical
study reports or ledgers were changed. The $3 validation reservation and $0.50 of
adapter reservations remain held in the original ledger pending authoritative Modal
billing; these are reservations, not claims about actual spend.

The private GPU deployment is live. The workspace and frontend remain local;
public API cutover and Vercel publication use the setup above and the same state.

## Smart / Frontier migration

The backend accepts only `routing_mode: "smart" | "frontier"`, defaulting new jobs
to Smart. It snapshots the selected model, endpoint and detector policy with the
job. Request IDs cannot be reused with a different mode. Legacy request replay
keeps its original manifest and recorded Verda events without inference. Legacy
queued jobs without a routing snapshot stop before dispatch.

The selector beside Send is remembered independently for each conversation, is
locked while a job is active, and displays only selected-mode readiness. The UI
does not display balances or holds. Backend accounting fields and the original
US$25 ceiling remain available. Never initialize a new ledger for migration.

For local setup, export `OPENROUTER_API_KEY` in the backend environment, then run
`python scripts/refresh_openrouter_catalog.py`. `YMIR_STOA_ROOT` still selects
GPU/native geometry assets. It does not select provider credentials or experiment
pacing. No experiment runtime, job, report or ledger is imported or modified.
The migration does not require changing or redeploying GPU checkpoints.

See [routing and validation](openrouter-routing.md) for endpoint pins, continuation
handling and verification scope. This change is delivered by normal Git push; no
manual deployment is part of the migration.

The browser now requests SSE for live job progress. Existing NDJSON clients remain
compatible. `deploy/check_streaming.py` is a disposable transport-only validation
app: it uses no production data, secrets, GPU functions or paid inference. Stop that
validation app after checking delivery. Clear chat rotates a conversation ID; it
does not reset or delete production jobs, labels or accounting state. New Frontier
jobs use explicit planning; Smart keeps the direct tool path.
