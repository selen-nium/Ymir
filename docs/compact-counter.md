# Compact Stoa Lens counter

Ask binds simple, unambiguous one-page counting requests to a compact DeepSeek
loop. The host supplies the page and target class; DeepSeek requests native-page
crops, accepts stable proposal IDs and stops inspecting once evidence is
sufficient. The host derives the count. Requests about schedules, rooms, multiple
sheets or ambiguous/localized objects retain the general harness.

Production routing remains D-FINE-L for its fourteen trained classes and Kimi
K3.1 Pro for other classes. D-FINE misses do not invoke Kimi automatically.
Grouped requests have one total and use one actual detector call per class.
Both calls consume the shared four-call allowance. Proposal confidence remains
uncalibrated and the host rejects duplicate, clipped and foreign-source evidence.

Native PDF text is retained. There is no dedicated OCR. Raster2Seq and verified
native geometry remain available through room/general requests. Printed areas
are model readings and cannot calibrate geometric measurement.

The Python/CLI entry point also accepts an explicit scope, for example:

```python
from stoa_lens.budget import LensBudget
from stoa_lens.service import run

result = run(
    pages, 'How many doors are shown?', 'var/example-doors',
    budget=LensBudget('var/example-budget.sqlite', 5),
    agent='compact_counter',
    count_scope={'page_index': 0, 'target_classes': ['door'],
                 'description': 'doors', 'backend': 'routed'},
)
```

The explicit `kimi_atomic` backend is for matched diagnostics. It uses the
historical atomic Kimi localization prompt without the production class prefix
or label filter. Ask never selects that override implicitly. This compact
version requires a complete detector reply before issuing a numeric count;
the historical harness also permitted a valid partial reply.

DeepSeek and Kimi use high reasoning and a 65,536-token output allowance.
Eight model responses, three tools per response, nine manual inspection views,
four detector requests, four native-text requests and a 600-second deadline
bound compact jobs. Failed calls consume allowances. Planning uses normal tool
mode; JSON mode is reserved for tool-free finalization/localization. Exhausted
tools are removed and remaining allowances accompany tool feedback.

Budget reservations share the same atomic ledger and queue while another active
request temporarily holds funds. Unknown charges retain their reservations.
There are no automatic API retries or inference replays after interruption.
An injected JobStore budget supports an isolated evaluation under an existing
ceiling without drawing from the separate default integration ledger.

The source runtime, its Ymir adaptations and actual file hashes are recorded in
`vendor/stoa-runtime/PROVENANCE.json`. Local assets, credentials, ledgers and
private evaluation references are not bundled with the runtime.
