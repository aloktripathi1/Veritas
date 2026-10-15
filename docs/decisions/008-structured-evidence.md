# ADR 008: Structured intermediate evidence
**Decision:** each claim stores doc, line range, verbatim quote, metric key, value, unit, period, claim type, note; saved as `evidence.json` per run.
**Why:** enables deterministic conflict detection (same metric+period), citation resolution, and review. Fields kept to what checks use.
**Trade-off:** metric keys come from a fixed list in `prompts/extract.md`; an unlisted metric falls to 'other' and is not conflict-checked.
