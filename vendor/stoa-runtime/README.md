# Ymir Ask runtime

This is a source snapshot of the local Stoa Lens integration and its required
helpers. `PROVENANCE.json` records upstream and packaged file hashes. Routing, evidence checks, receipts and the LLM reservation rules are preserved.
Ymir adds optional private Modal GPU dispatch, explicit compute reservations in
the same cap, and persistence callbacks before paid dispatch. Unknown compute
billing retains its reservation. VISTA's required inspection modules retain their MIT notice in
`VISTA-LICENSE`. Stoa project code retains its original ownership and terms.

Install through the repository's `requirements.txt`. Set `YMIR_STOA_ROOT` to the
existing Stoa asset workspace: pinned D-FINE checkpoint/configs, Raster2Seq
readiness/environment. Language models now use the backend-only
`OPENROUTER_API_KEY`. `YMIR_OPENROUTER_HOME` selects production endpoint catalogs
and pacing (default: `YMIR_DATA_DIR/lens/openrouter`), independently of Stoa assets
and experiment state. Set
`YMIR_DFINE_PYTHON` when its detector interpreter lives elsewhere. The local
launcher defaults to a sibling `stoa` checkout and reuses the current environments.

Model weights, provider credentials, historical runs, reference annotations and
budget ledgers are not bundled here. Tests use injected runners or analytic
fixtures; they never require paid inference. The initial snapshot tool refuses to overwrite this adapted runtime. Refresh
upstream in a separate directory and reconcile the listed adaptations. Never
refresh an active study's frozen runtime.

Production routing is explicit in `stoa_lens/model_config.py` and injected through
service, harness and compact counting. `transport.py` and `streaming.py` port the
reviewed OpenRouter transport/Argon adapter and stream accumulator; adopted file
hashes and adaptations are in `PROVENANCE.json`. The experimental hybrid router
and process-wide monkeypatch installer are not used. D-FINE handles trained
classes; Argon handles the rest. Historical helper modules remain in this snapshot
for provenance but no production language-model path imports the Verda transport.

Frontier planning is ported from Stoa's reviewed `PlanningCounter`; the provenance
section `frontier_planning` records source bytes and the general-question extension.
Smart retains the existing direct tool harness. No expanded experiment allowances
or paired-detector policy are adopted. Old configuration snapshots remain valid
and retain their recorded behavior.
