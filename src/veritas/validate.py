"""Deterministic brief validation. Citations are machine-checked references, not decoration.

What this proves: every cited id/line exists, quotes are verbatim (checked at registry build), every sentence in
the claim sections carries its own citation, every number is a WHOLE number present in the items cited by that
sentence, numbers are not attached to a different metric family, conflicts are disclosed, excluded-company
content and un-attributed recommendations are rejected.
What it does NOT prove: that a cited quote entails the sentence. That is covered only by the advisory LLM
check in entail.py, which shares the generator's blind spots.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .evidence import Calc
from .models import Conflict, Document, Evidence, Issue

SECTIONS = ["Snapshot", "Bull case", "Bear case", "Open questions"]
CITE = re.compile(r"\[(E\d+|C\d+|D\d+:L\d+(?:-L?\d+)?)\]")
NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)")
DATE_LIKE = re.compile(r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*(?:\s+\d{4})?|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}", re.I)
PERIOD_LIKE = re.compile(r"\b(Q[1-4]|FY\s?\d{2,4}(-\d{2})?|H[12])\b", re.I)
# recommendation / promotional language: allowed only when clearly attributed and distanced
REC = re.compile(r"strong buy|buy now|\bbuy\b|\bsell\b|accumulate|price target|target[^.]{0,20}₹|₹[\d,.]+[^.]{0,12}target|upside|multibagger|analyst consensus|consensus is|guaranteed|will double", re.I)
DISTANCE = re.compile(r"promotional|unregistered|unreliable|disregard|ignor|not evidence|not sebi|recommendation|claims?\b|claimed|implies|implied|embedded|not (be )?relied|no method|no basis|should not|unsupported|speculative|not a (reliable|guide)", re.I)
MAX_BODY_WORDS = 560

FAMILY_KEYWORDS = {
    "revenue": r"revenue|sales|turnover", "ebitda": r"ebitda|margin", "profit": r"profit|\bpat\b|earnings",
    "order": r"order book", "export": r"export", "promoter": r"pledg|holding|institution|\bfii\b|\bdii\b|promoter",
    "receivable": r"receivable|collection", "capex": r"capex|capital expenditure", "debt": r"debt|leverage",
    "tax": r"\bgst\b|\btax", "price": r"share price|price target|target", "datacentre": r"data.?cent", "copper": r"copper",
}
# most specific keys first: 'copper_price_change_pct' must not fall into the generic 'price' family
METRIC_FAMILIES = (("copper", "copper"), ("data_centre", "datacentre"), ("order_book", "order"), ("export", "export"),
                   ("pledge", "promoter"), ("promoter_holding", "promoter"), ("receivable", "receivable"), ("capex", "capex"),
                   ("debt", "debt"), ("tax", "tax"), ("ebitda", "ebitda"), ("revenue", "revenue"), ("pat", "profit"),
                   ("share_price", "price"), ("price_target", "price"))


def metric_family(metric: str) -> str | None:
    m = metric.lower()
    return next((fam for key, fam in METRIC_FAMILIES if m == key or m.startswith(key) or key in m), None)


class Metrics(BaseModel):
    sections_complete: bool
    body_words: int
    citations_total: int
    citations_valid: int
    claim_sentences: int
    claim_sentences_cited: int
    numbers_checked: int
    numbers_supported: int
    conflicts_required: int
    conflicts_disclosed: int


class ValidationResult(BaseModel):
    issues: list[Issue]
    metrics: Metrics

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)


def split_sections(md: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = None
    for line in md.splitlines():
        m = re.match(r"^#{2,3}\s*(.+?)\s*(\(.*\))?\s*$", line)
        if m:
            name = next((s for s in SECTIONS if m.group(1).lower().startswith(s.lower())), None)
            cur = name
            if name:
                out[name] = []
            continue
        if cur and line.strip():
            out[cur].append(line.strip())
    return out


def split_sentences(line: str) -> list[str]:
    line = re.sub(r"^\s*(?:[-*•]|\d+\.)\s+", "", line)
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+(?=[A-Z₹*\"“(])", line) if p.strip()]


def claim_sentences(md: str) -> list[tuple[str, str]]:
    """(section, sentence) for every sentence in the four sections."""
    secs = split_sections(md)
    return [(sec, s) for sec in SECTIONS for line in secs.get(sec, []) for s in split_sentences(line)]


def number_tokens(text: str) -> set[float]:
    text = DATE_LIKE.sub(" ", PERIOD_LIKE.sub(" ", text))
    out: set[float] = set()
    for tok in NUM.findall(text):
        t = tok.replace(",", "").rstrip(".")
        try:
            out.add(float(t))
        except ValueError:
            pass
    return {n for n in out if not (1900 <= n <= 2100 and float(n).is_integer())}


def validate_brief(md: str, ev: list[Evidence], calcs: list[Calc], docs: dict[str, Document],
                   conflicts: list[Conflict], lines_by_doc: dict[str, list[str]] | None = None,
                   excluded_docs: set[str] = frozenset(), excluded_markers: list[str] = ()) -> ValidationResult:
    issues: list[Issue] = []
    err = lambda code, msg: issues.append(Issue(severity="error", code=code, message=msg))  # noqa: E731
    warn = lambda code, msg: issues.append(Issue(severity="warning", code=code, message=msg))  # noqa: E731
    evd = {e.evidence_id: e for e in ev}
    cd = {c.calc_id: c for c in calcs}
    secs = split_sections(md)

    complete = all(s in secs and secs[s] for s in SECTIONS)
    for s in SECTIONS:
        if s not in secs or not secs[s]:
            err("missing_section", f"Section '{s}' missing or empty")

    body = "\n".join(line for s in SECTIONS for line in secs.get(s, []))
    words = len(re.findall(r"\S+", CITE.sub("", body)))
    if words > MAX_BODY_WORDS:
        per = {sec: len(re.findall(r"\S+", CITE.sub("", "\n".join(secs.get(sec, []))))) for sec in SECTIONS}
        err("too_long", f"Body is {words} words (limit {MAX_BODY_WORDS}); sections: {per}. DELETE at least {words - 480} words: target Snapshot<=70, Bull<=120, Bear<=140, Open questions<=150. "
                        "Drop the least material bullets and merge minor open questions; add nothing new")

    total = valid = 0
    n_sent = n_cited = nums = nums_ok = 0
    for sec in SECTIONS:
        for line in secs.get(sec, []):
            for sent in split_sentences(line):
                cites = CITE.findall(sent)
                stripped = CITE.sub("", sent)
                label_free = re.sub(r"^\*\*[^*]+\*\*:?\s*", "", stripped).strip()
                texts: list[str] = []
                cited_items: list[Evidence] = []
                has_calc = False
                for c in cites:
                    total += 1
                    t = None
                    if c[0] in "EC":
                        if c in evd:
                            t = evd[c].quote + f" {evd[c].value:g}" if evd[c].value is not None else evd[c].quote
                            cited_items.append(evd[c])
                            if evd[c].doc_id in excluded_docs:
                                err("excluded_source_cited", f"{c} comes from an excluded document")
                        elif c in cd:
                            t, has_calc = cd[c].text, True
                        else:
                            err("unknown_citation", f"{c} does not exist in the registry: {sent[:70]!r}")
                    else:
                        m = re.match(r"D(\d+):L(\d+)(?:-L?(\d+))?", c)
                        did, a, b = f"D{m.group(1)}", int(m.group(2)), int(m.group(3) or m.group(2))
                        if did not in docs or lines_by_doc is None or not (docs[did].body_start <= a <= b <= len(lines_by_doc[did])):
                            err("unknown_citation", f"{c} does not resolve to a document line: {sent[:70]!r}")
                        else:
                            t = " ".join(lines_by_doc[did][a - 1:b])
                            if did in excluded_docs:
                                err("excluded_source_cited", f"{c} comes from an excluded document")
                    if t is not None:
                        valid += 1
                        texts.append(t)

                words_in = len(label_free.split())
                digits = bool(number_tokens(label_free))
                our_view = label_free.lower().startswith("our view:")
                if words_in > 3:
                    is_question = sec == "Open questions" and (label_free.endswith("?") or not digits)
                    needs_cite = not our_view and not (is_question and not cites)
                    n_sent += 1
                    if cites:
                        n_cited += 1
                    elif our_view:
                        n_cited += 1
                        if digits:
                            err("our_view_with_numbers", f"[{sec}] an uncited 'Our view:' sentence may not contain numbers (cite the item, or drop the number): {sent[:80]!r}")
                    elif sec != "Open questions" or digits:
                        err("uncited_claim", f"[{sec}] sentence has no citation (cite it, or start it with 'Our view:' if it is your interpretation): {sent[:90]!r}")
                    elif sec == "Open questions":
                        n_cited += 1   # uncertainty statement without numbers
                    del needs_cite

                if re.search(r"\bunsourced\b", stripped, re.I) and cites:
                    err("mislabel", f"'unsourced' contradicts the citation. Say 'reported only by <source>, not confirmed by the company': {sent[:80]!r}")

                # whole-number support, per sentence
                support: set[float] = set()
                for t in texts:
                    support |= number_tokens(t)
                bps_gloss = re.search(r"basis point|\bbps?\b", label_free, re.I) is not None
                for n in number_tokens(label_free):
                    nums += 1
                    # definitional constants in a plain-English gloss of 'bps' (100 bps = 1 percentage point; 170 bps = 1.7 points)
                    if bps_gloss and (n in (1.0, 100.0) or any(abs(n * 100 - x) < 1e-6 for x in support)):
                        nums_ok += 1
                    elif any(abs(n - x) < 1e-9 for x in support):
                        nums_ok += 1
                    elif cites:
                        err("number_unsupported", f"number {n:g} is not a whole number in the items cited by this sentence: {sent[:90]!r}")
                    # metric binding: number matches only items of a different metric family
                    cands = [e for e in cited_items if any(abs(n - x) < 1e-9 for x in number_tokens(e.quote))]
                    fams_in_sentence = {f for f, rx in FAMILY_KEYWORDS.items() if re.search(rx, label_free, re.I)}
                    if cands and not has_calc and fams_in_sentence:
                        cfams = {metric_family(e.metric) for e in cands}
                        if None not in cfams and not (cfams & fams_in_sentence):
                            err("metric_mismatch", f"number {n:g} is taken from {sorted(c.metric for c in cands)} but the sentence is about {sorted(fams_in_sentence)}: {sent[:90]!r}")

                if REC.search(stripped) and not (DISTANCE.search(stripped) and (cites or our_view)):
                    err("injection_phrase", f"recommendation/promotional language without attribution and distance: {sent[:90]!r}")
                for mk in excluded_markers:
                    if mk.lower() in stripped.lower():
                        err("excluded_company_content", f"content from an unrelated company: {mk!r}")

    oq = "\n".join(secs.get("Open questions", []))
    oq_ids = set(re.findall(r"\[(E\d+)\]", oq))
    disclosed = 0
    for c in conflicts:
        need = set(c.evidence_ids)
        if len(oq_ids & need) >= min(2, len(need)):
            disclosed += 1
        else:
            err("conflict_undisclosed", f"Conflict on {c.metric} ({c.period}) not disclosed in Open questions with ids {sorted(need)}")

    for s in ("Bull case", "Bear case"):
        if s in secs and not any(line.startswith(("-", "*", "•")) or CITE.search(line) for line in secs[s]):
            warn("no_bullets", f"{s} has no bullet items")

    m = Metrics(sections_complete=complete, body_words=words, citations_total=total, citations_valid=valid,
                claim_sentences=n_sent, claim_sentences_cited=n_cited, numbers_checked=nums, numbers_supported=nums_ok,
                conflicts_required=len(conflicts), conflicts_disclosed=disclosed)
    return ValidationResult(issues=issues, metrics=m)
