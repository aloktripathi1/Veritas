"""Scripted model output for the bundled research_pack: a claims JSON that passes the registry checks and a brief that passes
validation. Used by the offline tests and by `veritas-eval` simulations (which are labelled as simulations, never as model quality)."""
import json

Q = lambda doc, quote, metric, value, period, unit="": {"doc_id": doc, "quote": quote, "metric": metric, "value": value, "period": period, "unit": unit}  # noqa: E731
REV = "| Revenue from operations | 1,248 | 1,058 | +18.0% |"
MARGIN = "| EBITDA margin | 11.2% | 12.9% | −170 bps |"
PLEDGE = "| Of which pledged (as % of promoter holding) | 4.1% | 6.0% | 11.5% |"
EXTRACTION = json.dumps([
    Q("D3", REV, "revenue", 1248, "Q1 FY27", "crore INR"),                                   # E01
    Q("D3", REV, "revenue", 1058, "Q1 FY26", "crore INR"),                                   # E02
    Q("D3", MARGIN, "ebitda_margin", 11.2, "Q1 FY27", "%"),                                  # E03
    Q("D3", MARGIN, "ebitda_margin", 12.9, "Q1 FY26", "%"),                                  # E04
    Q("D3", "| Profit after tax | 82 | 77 | +5.9% |", "pat", 82, "Q1 FY27", "crore INR"),    # E05
    Q("D1", "Revenue from operations rose to ₹1,428 crore, a 35% increase from the year-ago period", "revenue", 1428, "Q1 FY27", "crore INR"),  # E06
    Q("D1", "Revenue from operations rose to ₹1,428 crore, a 35% increase from the year-ago period", "revenue_growth_yoy_pct", 35, "Q1 FY27", "%"),  # E07
    Q("D8", PLEDGE, "promoter_pledge_pct_of_promoter_holding", 4.1, "Jun 2026", "%"),         # E08
    Q("D8", "| Promoters & promoter group | 58.2% | 58.2% | 58.4% |", "promoter_holding_pct", 58.2, "Jun 2026", "%"),  # E09
    Q("D7", "raising a demand of **₹46.3 crore** (including interest and penalty)", "tax_demand", 46.3, "FY2020-21 and FY2021-22", "crore INR"),  # E10
    Q("D2", "Bharuch capex is ₹620 crore in total, about 70 percent already spent", "capex_total", 620, "total", "crore INR"),  # E11
    Q("D2", "Bharuch capex is ₹620 crore in total, about 70 percent already spent", "capex_pct_spent", 70, "to date", "%"),     # E12
    Q("D6", "penalty of ₹8 lakh on Sarvottam Cable Network", "other", None, ""),   # unrelated company: must be dropped in robust mode
])
GOOD = """## Snapshot
Sarvottam Cables reported Q1 FY27 revenue of ₹1,248 crore, up 18.0% [E01][C1]. Profit after tax was ₹82 crore [E05].
## Bull case
- Revenue grew 18.0% year on year in Q1 FY27 [C1].
- Pledged shares fell to 4.1% of promoter holding [E08].
## Bear case
- Our view: one quarter of data is too little to judge how durable the growth is.
- The company received a GST demand of ₹46.3 crore and plans to appeal [E10].
## Open questions
- A news report says ₹1,428 crore but the filing says ₹1,248 crore; the filing is the primary source [E06][E01].
- The same report says 35% growth against 18.0% computed from the filing [E07][C1].
"""
