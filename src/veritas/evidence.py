"""Evidence registry: verify LLM-extracted quotes against the source, run calculations, detect conflicts."""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from . import calc
from .models import ClaimType, Conflict, Document, Evidence


class RawClaim(BaseModel):
    doc_id: str
    quote: str
    metric: str = "other"
    entity: str = ""
    value: float | None = None
    unit: str = ""
    period: str = ""
    claim_type: ClaimType = "other"
    note: str = ""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("**", "").replace("*", "").replace("`", "")).strip().lower()


def parse_claims(text: str) -> tuple[list[RawClaim], list[str]]:
    diags: list[str] = []
    start = text.find("[")
    if start < 0:
        return [], ["extraction: no JSON array in model output"]
    dec, pos, items = json.JSONDecoder(), start + 1, []
    while True:  # tolerate truncation: keep every complete object before the cut
        nxt = re.compile(r"\s*,?\s*").match(text, pos).end()
        if nxt >= len(text) or text[nxt] == "]":
            break
        try:
            obj, pos = dec.raw_decode(text, nxt)
        except json.JSONDecodeError:
            diags.append(f"extraction: output truncated/invalid after {len(items)} items; kept the complete ones")
            break
        items.append(obj)
    claims = []
    for i, it in enumerate(items):
        try:
            claims.append(RawClaim.model_validate(it))
        except ValidationError:
            diags.append(f"extraction: item {i} failed schema validation, dropped")
    return claims, diags


def locate(doc: Document, lines: list[str], quote: str) -> tuple[int, int] | None:
    """Find the quote in 1-3 consecutive body lines. Returns 1-indexed (start, end) or None."""
    q = _norm(quote)
    if not q:
        return None
    body = range(doc.body_start, len(lines) + 1)
    for size in (1, 2, 3):
        for s in body:
            e = s + size - 1
            if e > len(lines):
                break
            if q in _norm(" ".join(lines[s - 1:e])):
                return s, e
    return None


def _number_in_quote(value: float, quote: str) -> bool:
    q = quote.replace(",", "")
    forms = {f"{value:g}", f"{value:.1f}", f"{value:.2f}", str(int(value)) if value == int(value) else f"{value:g}"}
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", q) for f in forms)


PERIODISH = re.compile(r"Q[1-4]\s*FY\s*\d{2}|FY\s*\d{2,4}|[A-Za-z]{3}\s*\d{4}", re.I)
GROWTHISH = re.compile(r"growth|pct|margin|bps|yoy|change|share", re.I)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def table_period_problem(lines: list[str], line_no: int, value: float, period: str, metric: str) -> str | None:
    """If the quote sits in a markdown table, check the value's column header matches the claimed period.
    Returns a problem description or None."""
    row = lines[line_no - 1]
    if not row.lstrip().startswith("|"):
        return None
    top = line_no - 1
    while top > 0 and lines[top - 1].lstrip().startswith("|"):
        top -= 1
    header = _cells(lines[top])
    cells = _cells(row)
    idxs = [i for i, c in enumerate(cells) if i > 0 and re.search(rf"(?<![\d.]){re.escape(f'{value:g}')}(?![\d])", c.replace(",", ""))]
    if len(idxs) != 1 or idxs[0] >= len(header):
        return None                                     # ambiguous or ragged: do not guess
    h = header[idxs[0]]
    periodic_cols = [x for x in header[1:] if PERIODISH.search(x)]
    if not periodic_cols:
        return None
    if PERIODISH.search(h):
        a, b = re.sub(r"\W", "", h).lower(), re.sub(r"\W", "", period).lower()
        return None if (a in b or b in a) else f"value {value:g} is in column '{h}' but was labelled period '{period}'"
    return None if GROWTHISH.search(metric) else f"value {value:g} is from column '{h}' (not a period column) but metric is '{metric}'"


def build_registry(claims: list[RawClaim], docs: dict[str, Document], lines_by_doc: dict[str, list[str]],
                   allowed: set[str]) -> tuple[list[Evidence], list[str]]:
    diags: list[str] = []
    out: list[Evidence] = []
    for c in claims:
        d = docs.get(c.doc_id)
        if d is None:
            diags.append(f"dropped claim: unknown doc_id {c.doc_id!r}")
            continue
        if c.doc_id not in allowed:
            diags.append(f"dropped claim from excluded document {c.doc_id}")
            continue
        if len(c.quote.strip()) < 12:
            diags.append(f"dropped claim: quote too short to be evidence in {c.doc_id}: {c.quote!r}")
            continue
        loc = locate(d, lines_by_doc[c.doc_id], c.quote)
        if loc is None:
            diags.append(f"dropped claim: quote not found verbatim in {c.doc_id}: {c.quote[:60]!r}")
            continue
        if c.value is not None and not _number_in_quote(c.value, c.quote):
            diags.append(f"dropped claim: value {c.value} not present in its quote ({c.doc_id})")
            continue
        if c.value is not None and c.period:
            prob = table_period_problem(lines_by_doc[c.doc_id], loc[0], c.value, c.period, c.metric)
            if prob:
                diags.append(f"dropped claim: {prob} ({c.doc_id} line {loc[0]})")
                continue
        out.append(Evidence(evidence_id=f"E{len(out) + 1:02d}", doc_id=c.doc_id, line_start=loc[0], line_end=loc[1],
                            **c.model_dump(exclude={"doc_id"})))
    return out, diags


# ---------- calculations ----------
class Calc(BaseModel):
    calc_id: str
    text: str
    inputs: list[str]


_PERIOD = re.compile(r"Q([1-4])\s*FY\s*(\d{2})", re.I)


def _prior(period: str) -> str | None:
    m = _PERIOD.search(period)
    return f"Q{m.group(1)} FY{int(m.group(2)) - 1}" if m else None


def _key(p: str) -> str:
    return re.sub(r"\s+", "", p).upper()


def run_calculations(ev: list[Evidence]) -> list[Calc]:
    out: list[Calc] = []

    def add(text: str, ids: list[str]):
        out.append(Calc(calc_id=f"C{len(out) + 1}", text=text, inputs=ids))

    by = {(e.doc_id, e.metric, _key(e.period)): e for e in ev if e.value is not None and e.metric != "other"}
    for (doc, metric, per), e in list(by.items()):
        pr = _prior(e.period)
        old = by.get((doc, metric, _key(pr))) if pr else None
        if not old or old.value == 0:
            continue
        if e.unit == "%" and old.unit == "%":
            add(f"{metric}: {old.value:g}% -> {e.value:g}% ({e.period} vs {pr}) = {calc.bps_change(e.value, old.value):+.0f} bps", [e.evidence_id, old.evidence_id])
        else:
            add(f"{metric}: {e.value:g} vs {old.value:g} ({e.period} vs {pr}) = {calc.pct_change(e.value, old.value):+.1f}% YoY", [e.evidence_id, old.evidence_id])
    # margin recomputation from EBITDA / revenue
    for (doc, metric, per), e in list(by.items()):
        r = by.get((doc, "revenue", per))
        if metric == "ebitda" and r:
            add(f"EBITDA / revenue ({e.period}) = {calc.margin_pct(e.value, r.value):.1f}%", [e.evidence_id, r.evidence_id])
    # promoter pledge as % of total shares (same document, same period)
    for e in ev:
        if e.metric == "promoter_pledge_pct_of_promoter_holding" and e.value is not None:
            h = next((x for x in ev if x.metric == "promoter_holding_pct" and x.doc_id == e.doc_id
                      and _key(x.period) == _key(e.period) and x.value is not None), None)
            if h:
                add(f"Pledged shares as % of TOTAL shares ({e.period}) = {e.value:g}% x {h.value:g}% = {calc.pledge_of_total(e.value, h.value):.1f}% "
                    f"(the {e.value:g}% is a share of promoter holding only)", [e.evidence_id, h.evidence_id])
    # capex progress
    tot = next((x for x in ev if x.metric == "capex_total" and x.value is not None), None)
    pct = next((x for x in ev if x.metric == "capex_pct_spent" and x.value is not None), None)
    if tot and pct:
        sp, rem = calc.spent_and_remaining(tot.value, pct.value)
        add(f"Capex: {pct.value:g}% of {tot.value:g} crore spent = {sp:.0f} crore; remaining = {rem:.0f} crore", [tot.evidence_id, pct.evidence_id])
    # tip-sheet target vs price
    tgt = next((x for x in ev if x.metric == "price_target" and x.value is not None), None)
    px = next((x for x in ev if x.metric == "share_price" and x.value is not None), None)
    if tgt and px:
        add(f"Tip-sheet target {tgt.value:g} vs last reported price {px.value:g} implies {calc.implied_upside(tgt.value, px.value):+.0f}% "
            f"(the tip sheet itself claims 60%; prices are from different dates)", [tgt.evidence_id, px.evidence_id])
    # tax demand sized against the latest-period profit (different time bases, stated in the text)
    tax = next((x for x in ev if x.metric == "tax_demand" and x.value is not None), None)
    pats = [x for x in ev if x.metric == "pat" and x.value is not None and _PERIOD.search(x.period)]
    if tax and pats:
        latest = max(pats, key=lambda x: (int(_PERIOD.search(x.period).group(2)), int(_PERIOD.search(x.period).group(1))))
        add(f"GST demand {tax.value:g} crore = {tax.value / latest.value * 100:.0f}% of {latest.period} profit after tax ({latest.value:g} crore); "
            f"demand covers earlier years, profit is one quarter: sizes the exposure only",
            [tax.evidence_id, latest.evidence_id])
    return out


GAP_CHECKS = {
    "a valuation multiple or market cap": r"p/e\b|price[- ]to[- ]earnings|ev/ebitda|market cap|valuation",
    "a cash-flow statement": r"cash flow|operating cash|free cash",
    "an absolute debt or cash figure": r"total debt|gross debt|net debt of|borrowings of|cash and (bank|equivalents)",
    "full-year results": r"full[- ]year results|audited|annual report|FY26 (revenue|results|profit)",
    "a dividend or buyback": r"dividend|buyback",
}


def data_gaps(texts: list[str]) -> Calc | None:
    blob = "\n".join(texts).lower()
    missing = [k for k, rx in GAP_CHECKS.items() if not re.search(rx, blob, re.I)]
    if not missing:
        return None
    return Calc(calc_id="", text=f"Data check: none of the {len(texts)} relevant documents has " + "; ".join(missing), inputs=[])


# ---------- conflicts ----------
POINT_IN_TIME = {"promoter_pledge_pct_of_promoter_holding", "promoter_holding_pct", "receivable_days", "share_price"}


def detect_conflicts(ev: list[Evidence], calcs: list[Calc], docs: dict[str, Document]) -> list[Conflict]:
    out: list[Conflict] = []
    groups: dict[tuple[str, str], list[Evidence]] = {}
    for e in ev:
        if e.value is not None and e.metric not in ("other", "price_target"):
            groups.setdefault((e.metric, _key(e.period)), []).append(e)
    for (metric, per), g in groups.items():
        ds = {e.doc_id for e in g}
        vals = sorted({e.value for e in g})
        if len(ds) > 1 and len(vals) > 1 and (vals[-1] - vals[0]) / max(abs(vals[-1]), 1e-9) > 0.005:
            out.append(Conflict(metric=metric, period=g[0].period, evidence_ids=[e.evidence_id for e in g], values=vals,
                                kind="numeric_conflict",
                                explanation=f"Different sources give different values for the same metric and period; "
                                            f"compare source types/dates: {[(e.doc_id, docs[e.doc_id].published, docs[e.doc_id].doc_type) for e in g]}"))
    for metric in POINT_IN_TIME:
        g = [e for e in ev if e.metric == metric and e.value is not None]
        pubs = {e.doc_id: docs[e.doc_id].published for e in g}
        if len(set(pubs.values())) > 1:
            vals = {e.evidence_id: e.value for e in g}
            if (max(vals.values()) - min(vals.values())) / max(max(vals.values()), 1e-9) > 0.2:
                out.append(Conflict(metric=metric, period="multiple dates", evidence_ids=list(vals), values=sorted(set(vals.values())),
                                    kind="stale_vs_new",
                                    explanation=f"Same metric reported at different dates: {pubs}. Older figure is superseded unless shown otherwise."))
    # reported growth vs computed growth: one conflict per period, listing every disagreeing evidence id
    growth = next((c for c in calcs if c.text.startswith("revenue:") and "% YoY" in c.text), None)
    if growth:
        computed = float(re.search(r"= ([+-][\d.]+)% YoY", growth.text).group(1))
        bad: dict[str, list[Evidence]] = {}
        for e in ev:
            if e.metric == "revenue_growth_yoy_pct" and e.value is not None and abs(computed - e.value) > 1.0:
                bad.setdefault("all", []).append(e)
        for g in bad.values():
            existing = next((c for c in out if c.metric == "revenue_growth_yoy_pct"), None)
            if existing:  # same conflict already found across documents: enrich it, do not duplicate
                existing.evidence_ids = list(dict.fromkeys(existing.evidence_ids + [e.evidence_id for e in g] + growth.inputs))
                existing.values = sorted({computed, *existing.values})
                existing.explanation += f" Also disagrees with growth computed from the filing's figures ({growth.text})."
                continue
            out.append(Conflict(metric="revenue_growth_yoy_pct", period=g[0].period,
                                evidence_ids=list(dict.fromkeys([e.evidence_id for e in g] + growth.inputs)),
                                values=sorted({computed, *(e.value for e in g)}), kind="numeric_conflict",
                                explanation=f"Reported growth {sorted({e.value for e in g})} disagrees with growth computed from filing figures ({growth.text})."))
    return out
