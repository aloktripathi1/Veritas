# ADR 007: Direct provider call, no agent framework
**Decision:** ~60 lines in `llm.py`: Anthropic SDK, plus a `claude` CLI provider (tools disabled) and a scripted test double.
**Why:** LangChain/LlamaIndex/LangGraph solve retrieval/graph state problems we do not have. **Model:** claude-sonnet-5-5 for strong instruction following at moderate cost; no `temperature` (the model rejects it), so runs vary slightly (measured in evaluation).
**Revisit when:** multi-step tool use or checkpointing is needed.
