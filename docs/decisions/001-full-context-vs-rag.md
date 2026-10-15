# ADR 001: Full-context analysis instead of RAG
**Problem:** 8 documents, ~10 KB total (~3k tokens). **Options:** conventional RAG with embeddings/vector DB; full-context prompting.
**Decision:** put every document in context, with line numbers.
**Trade-offs:** no recall risk from chunking (a missed chunk would hide a conflict); simple. Does not scale.
**Rejected:** RAG/GraphRAG add infrastructure and a retrieval failure mode with no benefit at this size (the research report recommends them for large corpora).
**Revisit when:** a pack exceeds the context window or cost, or per-claim latency matters (then metadata-aware lexical retrieval first).
