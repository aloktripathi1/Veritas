# ADR 003: Staged evidence extraction + synthesis vs single pass
**Problem:** single-pass output can look right while citing loosely (baseline run 1 cited a line that did not contain the number it was attached to).
**Decision:** extract claims with verbatim quotes -> registry -> synthesise from the registry only.
**Trade-offs:** 2-3 LLM calls, ~5-6x latency (about 100 s vs 16 s) (see evaluation). Extraction can truncate; parser salvages complete items.
**Rejected:** single pass (kept as baseline for comparison).
**Revisit when:** measurements show the baseline's citation validity matches the staged pipeline on larger packs.
