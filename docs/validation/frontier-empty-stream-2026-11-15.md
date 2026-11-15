# Frontier empty-stream recovery

A live piping question selected Frontier correctly (Sol/high, 65,536 output tokens) and recorded an execution plan. The next OpenRouter stream ended with zero chunks: no assistant content, tool calls, generation ID, usage, or finish marker. The adapter accepted an empty assistant message; final-JSON validation then forced a tools-disabled response. The resulting review answer cited only the overview because VISTA was never called.

The stream adapter now rejects empty assistant payloads and retains a failure receipt. One bounded next conversation response can continue the last valid plan/history with remaining tools. The failed request consumes its response slot. SDK retries stay zero, unknown charges retain their reservation, provider routing stays fixed, and all evidence gates and execution limits stay intact. Repeated empty responses and empty final responses remain bounded.

Validation: 216 backend tests passed. Regression fixtures cover the observed zero-chunk SDK stream, failure/unknown-billing receipts, plan → empty response → registered detail inspection → source-cited answer, and repeated-empty termination. Test traffic is injected; no paid inference is used by these fixtures. Existing job receipts, experiments, and ledger rows are preserved.
