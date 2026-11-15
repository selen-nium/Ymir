# Ymir Ask runtime

This is a source snapshot of the local Stoa Lens integration and its required
helpers. `PROVENANCE.json` records upstream and packaged file hashes. Routing, evidence checks, receipts and the LLM reservation rules are preserved.
Ymir adds optional private Modal GPU dispatch, explicit compute reservations in
the same cap, and persistence callbacks before paid dispatch. Unknown compute
billing retains its reservation. VISTA's required inspection modules retain their MIT notice in
`VISTA-LICENSE`. Stoa project code retains its original ownership and terms.

Install through the repository's `requirements.txt`. Set `YMIR_STOA_ROOT` to the
existing Stoa asset workspace: pinned D-FINE checkpoint/configs, Raster2Seq
readiness/environment, provider catalog and backend credentials. Set
`YMIR_DFINE_PYTHON` when its detector interpreter lives elsewhere. The local
launcher defaults to a sibling `stoa` checkout and reuses the current environments.

Model weights, provider credentials, historical runs, reference annotations and
budget ledgers are not bundled here. Tests use injected runners or analytic
fixtures; they never require paid inference. The initial snapshot tool refuses to overwrite this adapted runtime. Refresh
upstream in a separate directory and reconcile the listed adaptations. Never
refresh an active study's frozen runtime.
