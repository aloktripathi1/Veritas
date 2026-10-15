# ADR 006: Deterministic checks first, no LLM judge
**Decision:** verify citation existence, line range, verbatim quotes, numbers-in-cited-text, section/length, conflict disclosure, leakage by code.
**Trade-offs:** cannot judge whether a quote *entails* a claim (Q11 open). Number check can reject a correct claim whose number is derived (it must then cite C##).
**Rejected:** LLM-as-judge as the gate, because it shares the generator's blind spots and is non-deterministic. **Revisit when:** entailment errors are observed that numeric checks miss; add it as an advisory second opinion, labelled as such.

**Update after independent review:** the review showed deterministic checks did not stop false-but-cited briefs. Added per-sentence citation, whole-number matching and metric-family binding (deterministic), plus an **advisory** LLM entailment judge (`entail.py`) as a second opinion. It is not the gate by itself: it feeds the repair loop and, if it still says "unsupported", the run fails loudly. Limits: same model family as the writer, non-deterministic, 3-6 "partial" nitpicks per brief.
