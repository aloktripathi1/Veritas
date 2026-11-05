# ADR 009: A LangGraph engine next to the hand-written pipeline
**Decision:** keep `pipeline.run` as the reference and default; add `graph.py`, an opt-in `--engine graph` that runs robust mode as a LangGraph `StateGraph`. Both call the same helpers (`screen`, `calculate`, `registry_context`, `repair_user_message`, `record_entailment`, the validators, `entail.judge`), extracted from `run()` without changing it. Optional extra `[graph]`, `langgraph==1.2.20`.
**Context:** [ADR 007](007-direct-api-no-framework.md) said to revisit when checkpointing or multi-step control flow was needed. Robust mode now has two branch points (abort on thin evidence; judge/repair loop with a budget) and a long, partly stateful run, which is where the hand-written version is hardest to inspect.
**What the framework gave:**
- *Explicit state.* Every field a stage reads or writes is declared in `VeritasState`, instead of living in the locals of one long function.
- *Checkpointing.* With `InMemorySaver` and a `thread_id`, the state after each node can be read back (`node_trace`, `get_state`); the MCP server uses the final state to re-validate edited briefs. Tests assert the node order of a run, including the repair loop.
- *Inspectable edges.* The abort and repair decisions are two small routing functions (`route_after_verify`, `route_after_validate`/`route_after_judge`) that tests call and that the Mermaid diagram in `architecture.md` mirrors. "Judge skipped" is an edge, not an `if` inside a loop.
**What it cost:**
- *A dependency.* `langgraph` pulls in `langchain-core`, `langgraph-checkpoint`, `langgraph-sdk` and others; hence the optional extra and an exact pin.
- *Indirection and a second code path.* Control flow is spread over nodes and edges, and the loop is no longer one readable block. Two engines must stay equivalent, so the suite runs eight scripted scenarios through both and compares briefs, issues, prompts, calls and repairs. The structural differences are listed below.
- *Overhead.* Offline with a scripted model, one robust run took about 4.6 ms classic and 19.4 ms graph (mean of 20 runs on a development machine; checkpoint serialisation of the state). Negligible next to model latency, but not zero.
- *API churn risk.* MCP shows how fast these SDKs move (`FastMCP` is renamed in 2.x); pins limit the surprise.
**Where the graph deliberately differs in structure (not in output):** `abort` and `finalize` are nodes; redaction evidence is created in `calculate` (evidence ids and the abort count depend on the registry being built first).
**Not claimed:** the graph engine does not make runs better or more reliable; the same prompts and validators produce the same results. No live comparison was made for this ADR.
**Revisit when:** adding human-in-the-loop review, persistent checkpoints (SQLite/Postgres saver) or parallel branches; or if the classic engine can be retired.
