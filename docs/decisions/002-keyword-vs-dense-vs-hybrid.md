# ADR 002: No retrieval layer (keyword vs dense vs hybrid)
**Decision:** none, a consequence of ADR 001.
**Why:** the task is cross-document comparison, which needs everything visible, not top-k passages.
**Revisit when:** corpus > ~100 documents. Start with BM25 plus metadata (date, source type) filters; add dense only if measured recall misses.
