# ADR 005: Deterministic financial calculations
**Decision:** growth, margins, bps, pledge-as-%-of-total, capex remaining, implied upside are Python (`calc.py`, unit-tested). The brief cites them as C##.
**Also:** reported growth is checked for attainability under rounding (`pct_change_within_rounding`); run 3 flagged EBITDA +2.6% vs computed +2.9% as a rounding question instead of calling it an error.
**Rejected:** letting the LLM compute. **Revisit when:** needing formulas beyond simple ratios.
