import pytest

from veritas import calc
from veritas.evidence import build_registry, detect_conflicts, parse_claims, run_calculations
from veritas.loader import load_documents


def test_calc_functions():
    assert calc.pct_change(1248, 1058) == pytest.approx(17.96, abs=0.01)
    assert calc.margin_pct(140, 1248) == pytest.approx(11.22, abs=0.01)
    assert calc.bps_change(11.2, 12.9) == pytest.approx(-170)
    assert calc.pledge_of_total(4.1, 58.2) == pytest.approx(2.386, abs=0.001)
    assert calc.spent_and_remaining(620, 70) == (434.0, 186.0)
    assert calc.implied_upside(1450, 1012) == pytest.approx(43.3, abs=0.1)
    assert calc.normalise_number("₹1,428 crore") == 1428
    with pytest.raises(ValueError):
        calc.pct_change(1, 0)


def test_reported_growth_is_within_rounding_of_filing_inputs():
    assert calc.pct_change_within_rounding(140, 136, 2.6)
    assert calc.pct_change_within_rounding(82, 77, 5.9)
    assert not calc.pct_change_within_rounding(1248, 1058, 35.0)


def _claim(doc, quote, metric, value, period, unit=""):
    return {"doc_id": doc, "quote": quote, "metric": metric, "value": value, "period": period, "unit": unit}


def test_registry_rejects_fabricated_quotes_and_wrong_values(pack):
    import json
    docs, _ = load_documents(pack)
    by = {d.doc_id: d for d in docs}
    lines = {d.doc_id: d.lines for d in docs}
    raw = json.dumps([
        _claim("D3", "Revenue from operations | 1,248 | 1,058", "revenue", 1248, "Q1 FY27"),
        _claim("D3", "Revenue was 9,999 crore", "revenue", 9999, "Q1 FY27"),       # fabricated quote
        _claim("D3", "Revenue from operations | 1,248 | 1,058", "revenue", 5555, "Q1 FY27"),  # value not in quote
        _claim("D99", "x", "revenue", 1, ""),                                       # unknown doc
    ])
    claims, d = parse_claims(raw)
    ev, diags = build_registry(claims, by, lines, {d.doc_id for d in docs})
    assert len(ev) == 1 and ev[0].line_start == ev[0].line_end
    assert len(diags) == 3


def test_parser_salvages_truncated_json():
    claims, d = parse_claims('[{"doc_id":"D1","quote":"a"},{"doc_id":"D2","quo')
    assert len(claims) == 1 and any("truncated" in x for x in d)


def test_conflict_detection_and_period_scoping(pack):
    from veritas.models import Evidence
    mk = lambda i, doc, m, v, p, u="": Evidence(evidence_id=i, doc_id=doc, line_start=1, line_end=1, quote="q", metric=m, value=v, period=p, unit=u)
    docs, _ = load_documents(pack)
    by = {d.doc_id: d for d in docs}
    ev = [mk("E01", "D3", "revenue", 1248, "Q1 FY27"), mk("E02", "D1", "revenue", 1428, "Q1 FY27"),
          mk("E03", "D3", "revenue", 1058, "Q1 FY26"),     # different period: not a conflict
          mk("E04", "D4", "promoter_pledge_pct_of_promoter_holding", 35, "Mar 2024"),
          mk("E05", "D8", "promoter_pledge_pct_of_promoter_holding", 4.1, "Jun 2026")]
    cs = detect_conflicts(ev, run_calculations(ev), by)
    kinds = {(c.metric, c.kind) for c in cs}
    assert ("revenue", "numeric_conflict") in kinds
    assert ("promoter_pledge_pct_of_promoter_holding", "stale_vs_new") in kinds
    rev = next(c for c in cs if c.metric == "revenue")
    assert set(rev.evidence_ids) == {"E01", "E02"}


def test_calculations_denominator_and_capex():
    from veritas.models import Evidence
    mk = lambda i, m, v, p, u="": Evidence(evidence_id=i, doc_id="D8", line_start=1, line_end=1, quote="q", metric=m, value=v, period=p, unit=u)
    ev = [mk("E01", "promoter_holding_pct", 58.2, "Jun 2026"), mk("E02", "promoter_pledge_pct_of_promoter_holding", 4.1, "Jun 2026"),
          mk("E03", "capex_total", 620, "total"), mk("E04", "capex_pct_spent", 70, "to date"),
          mk("E05", "ebitda_margin", 11.2, "Q1 FY27", "%"), mk("E06", "ebitda_margin", 12.9, "Q1 FY26", "%")]
    texts = " | ".join(c.text for c in run_calculations(ev))
    assert "2.4% of TOTAL" not in texts and "= 2.4%" in texts
    assert "434 crore" in texts and "186 crore" in texts and "-170 bps" in texts


def test_growth_conflict_reported_once_with_all_sources(pack):
    from veritas.models import Evidence
    mk = lambda i, doc, m, v, p, u="": Evidence(evidence_id=i, doc_id=doc, line_start=1, line_end=1, quote="q", metric=m, value=v, period=p, unit=u)
    docs, _ = load_documents(pack)
    by = {d.doc_id: d for d in docs}
    ev = [mk("E01", "D3", "revenue", 1248, "Q1 FY27"), mk("E02", "D3", "revenue", 1058, "Q1 FY26"),
          mk("E03", "D1", "revenue_growth_yoy_pct", 35, "Q1 FY27", "%"), mk("E04", "D5", "revenue_growth_yoy_pct", 35, "Q1 FY27", "%")]
    cs = [c for c in detect_conflicts(ev, run_calculations(ev), by) if c.metric == "revenue_growth_yoy_pct"]
    assert len(cs) == 1 and {"E03", "E04", "E01", "E02"} <= set(cs[0].evidence_ids)
