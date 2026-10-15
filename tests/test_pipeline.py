"""Integration tests with a scripted (offline) model. These test OUR pipeline, not model quality;
live-model behaviour is covered by tests/test_live.py and docs/evaluation.md."""
import json


from veritas.cli import main
from veritas.llm import ScriptedLLM
from veritas.pipeline import run
from veritas.render import render

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


def test_end_to_end_robust_with_scripted_model(pack, tmp_path):
    llm = ScriptedLLM([EXTRACTION, GOOD])
    res = run("robust", "SRVCABLE", "Sarvottam Cables Ltd", pack, llm, use_entailment=False)
    assert res.validation.ok, res.validation.issues
    assert len(res.evidence) == 12 + 1                     # Nagpur claim dropped; +1 registry entry for the redacted instruction
    assert any(c.metric == "revenue" for c in res.conflicts)
    md = render(res, "SRVCABLE", "2026-09-23")
    assert "## Sources" in md and "L16" in md and "evidence.json" in md
    assert "excluded as a different company: nagpurcitytimes" in md


def test_injection_text_never_reaches_the_model_in_robust_mode(pack):
    llm = ScriptedLLM([EXTRACTION, GOOD])
    run("robust", "SRVCABLE", "Sarvottam Cables Ltd", pack, llm, use_entailment=False)
    sent = " ".join(s + u for s, u in llm.calls).lower()
    assert "ignore all previous instructions" not in sent
    assert "redacted: suspected embedded instruction" in sent
    assert "untrusted data" in sent                         # trust boundary stated in system prompt
    baseline = ScriptedLLM([GOOD])
    run("baseline", "SRVCABLE", "Sarvottam Cables Ltd", pack, baseline)
    assert "ignore all previous instructions" in baseline.calls[0][1].lower()   # baseline has no defence (by design)


def test_model_that_obeys_injection_is_rejected_then_repaired(pack):
    obeyed = GOOD.replace("Sarvottam Cables reported", "Analyst consensus is positive and this is a strong buy [E01]. Sarvottam Cables reported", 1)
    llm = ScriptedLLM([EXTRACTION, obeyed, GOOD])
    res = run("robust", "SRVCABLE", "Sarvottam Cables Ltd", pack, llm, use_entailment=False)
    assert res.repaired and res.llm_calls == 3 and res.validation.ok


def test_repair_is_bounded_and_failure_is_reported(pack):
    obeyed = GOOD.replace("Sarvottam Cables reported", "This is a strong buy [E01]. Sarvottam Cables reported", 1)
    llm = ScriptedLLM([EXTRACTION, obeyed, obeyed, obeyed, "UNUSED"])
    res = run("robust", "SRVCABLE", "Sarvottam Cables Ltd", pack, llm, use_entailment=False)
    assert res.llm_calls == 4 and res.repairs == 2 and not res.validation.ok   # two repairs at most, then fail loudly


def test_fabricated_citation_in_output_fails_validation(pack):
    bad = GOOD.replace("[E05]", "[E99]")
    res = run("structured", "SRVCABLE", "Sarvottam Cables Ltd", pack, ScriptedLLM([EXTRACTION, bad]))
    assert not res.validation.ok and any(i.code == "unknown_citation" for i in res.validation.issues)


def test_cli_missing_directory_returns_clean_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SRV_PROVIDER", "claude-cli")
    assert main(["SRVCABLE", str(tmp_path / "missing"), "--out", str(tmp_path / "o.md")]) == 2
    assert "not found" in capsys.readouterr().err


def test_progress_messages_report_real_events(pack):
    msgs: list[str] = []
    res = run("robust", "SRVCABLE", "Sarvottam Cables Ltd", pack, ScriptedLLM([EXTRACTION, GOOD]), use_entailment=False, progress=msgs.append)
    text = "\n".join(msgs)
    assert "Read 8 documents" in text and "EXCLUDED (different company)" in text and "instruction-like text redacted" in text
    assert f"{len(res.evidence) - 1} verified verbatim" in text or "verified verbatim" in text
    assert "conflicts between sources: 2" in text and "numeric_conflict: revenue = 1248 vs 1428" in text
