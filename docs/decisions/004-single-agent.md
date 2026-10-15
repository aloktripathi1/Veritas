# ADR 004: One orchestrated pipeline, not multiple agents
**Decision:** fixed sequence of two LLM calls (+1 repair) with deterministic stages between them.
**Why:** no stage needs autonomy or tools; debate/critic agents would add cost and non-determinism without a measured failure they fix.
**Revisit when:** a failure class appears that a separate critic with different context demonstrably catches.
