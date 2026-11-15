# Smart and Frontier routing

New conversations start in Smart. The composer remembers each conversation's mode
and records it beside each question's source scope. Switching modes preserves the
thread and selected drawings. Submission snapshots the mode; the selector stays
locked until that job finishes, including during reconnect. Suggestions use the
same submit path. Six previous completed turns remain context across switches;
prior answers and IDs are never registered as new evidence. Clear chat starts a new
server conversation ID and restores the Smart default. Earlier jobs, saved labels
and drawing selection remain intact; reload loads only the new conversation.

| Mode or tool | OpenRouter model | Pinned endpoint |
| --- | --- | --- |
| Smart orchestrator | `deepseek/deepseek-v4.1-flash-1017` | `particle-ai/us` |
| Frontier orchestrator | `openai/gpt-6.1-sol` | `openai` |
| Atomic crop detector | `google/gemini-4-argon` | `google-vertex/global` |

Both orchestrators use high reasoning, a 65,536-token output allowance and the same
bounded tools. The backend accepts mode names only; client model IDs and arbitrary
configuration fields are rejected. Each new job records its mode, model/provider
snapshot, sources and context. Runtime manifests and results also record these
identities; billing receipts retain returned model/provider identities and generation
IDs. Trace projection uses recorded identities, while legacy DeepSeek/Kimi jobs
retain their original labels and events.

New Frontier jobs add an explicit planning step in the same model conversation.
Compact counting uses the reviewed Stoa `coverage_plan` tool: the recorded regions
cover the registered page, and coverage is checked against actual inspection and
complete detection receipts. General questions use `execution_plan`, a short
checklist constrained to registered page IDs and available tools. Native geometry
and printed readings keep their existing source-capability policy. The plan is not
evidence and cannot establish counts or measurements. Smart/DeepSeek keeps the
existing harness without either explicit planning tool. Both retain the current
tool, output-token and deadline limits; there is no separate planner model.
Pre-planning job snapshots keep their original execution policy.

D-FINE handles its fourteen verified trained classes, including explicit aliases.
Argon handles other exact classes, including subtypes that cannot safely be mapped
to a trained head. A trained-class failure never invokes Argon as a fallback.
Argon receives one crop and returns validated normalized boxes, status and
uncertainties. Existing class filtering, coordinate mapping, proposal review and
host counts remain in place. Compact counting uses the same routing snapshot and
class policy. The separate research experiment's paired D-FINE/Argon policy is not
part of production.

VISTA inspection, native PDF text/vector inspection, IFC/SVG/DXF geometry,
Raster2Seq room outlines, calibrated distances and room areas remain available
according to registered source capabilities. Drawing-footprint previews do not
become object-count evidence. Printed areas remain model readings; measurements
need source-derived calibration.

The transport sends one pinned provider with `allow_fallbacks: false` and
`require_parameters: true`. The SDK uses `max_retries=0`; no route escalates to the
other. Stream assembly retains tool arguments and reasoning continuation fields,
including opaque encrypted blocks and signatures, and resends the full recorded
assistant message. Tool/deadline limits and operation-boundary cancellation remain
unchanged. Existing evidence-only finalization after a failed planning response is
a new bounded response, never a replay of that failed dispatch.

Readiness performs no network or inference. It checks backend credentials, fresh
per-model endpoint metadata, image/tool/output capabilities and shared GPU tools.
Smart and Frontier are independent; missing Sol metadata does not block Smart.
Catalog refresh makes public metadata requests only. Before dispatch, the selected
endpoint is revalidated. See [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
and [reasoning continuation](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

The existing production ledger and ceiling are retained. New reservations allow
only the three approved OpenRouter models; historical charges and unresolved holds
remain readable and count toward the same ceiling. Conservative full-context
reservations include catalog pricing overrides and per-image/request fees. Verified
OpenRouter `usage.cost`, including costs received in a partial stream, settles a
charge. Missing or invalid billing retains the reservation. A provider 429 before
acceptance settles zero; an ambiguous failure retains its hold. No token-price
estimate is substituted for billing. Detailed receipts stay on the backend; the
app presents spending-limit stops as an incomplete request with evidence available
for review. See [OpenRouter usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

The maintained runtime ports reviewed Stoa source without importing the active
Frontier experiment or installing its process patches. Production catalog/pacing
state is separate from that experiment. `vendor/stoa-runtime/PROVENANCE.json`
records source hashes and port adaptations. Archived research records remain
unchanged. This migration's transport and UI checks are independent of the ongoing
model-quality evaluation.

Browser event transport negotiates `text/event-stream` so Modal delivers progress
as events arrive; legacy API clients continue to receive NDJSON. Both read the
same sequenced durable log, and reconnect uses the existing `after` cursor without
resubmitting a question. The UI distinguishes sending, an actual queued job, model
work, tool work and final checks, and shows elapsed time while a request is active.
See [Modal streaming delivery](https://modal.com/docs/guide/streaming-endpoints).

An empty provider stream is recorded as `EmptyAssistantResponse`, with its raw stream and unknown billing reservation retained. One subsequent conversation response may continue the recorded plan using remaining tools, within the original response and deadline allowances. The SDK never replays the HTTP request. A second empty response proceeds to bounded finalization; an empty final response stops. This prevents an empty stream from being mistaken for an invalid final answer that disables detail inspection.
