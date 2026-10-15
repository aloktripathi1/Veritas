from veritas.loader import load_documents
from veritas.models import Conflict, Evidence
from veritas.validate import validate_brief

EV = [Evidence(evidence_id="E01", doc_id="D3", line_start=1, line_end=1, quote="Revenue 1,248 crore", value=1248),
      Evidence(evidence_id="E02", doc_id="D1", line_start=1, line_end=1, quote="revenue rose to ₹1,428 crore", value=1428)]
GOOD = """## Snapshot
Cable maker reported revenue of 1,248 crore in the quarter [E01].
## Bull case
- Revenue reached 1,248 crore as reported in the filing [E01].
## Bear case
- Margins fell sharply compared with last year in this quarter [E01].
## Open questions
- Filing says 1,248 crore but a news story says 1,428 crore; the filing is primary [E01][E02].
"""


def v(md, conflicts=(), **kw):
    docs, _ = load_documents(__import__("tests.conftest", fromlist=["PACK"]).PACK)
    return validate_brief(md, EV, [], {d.doc_id: d for d in docs}, list(conflicts), **kw)


C = Conflict(metric="revenue", period="Q1 FY27", evidence_ids=["E01", "E02"], values=[1248, 1428], kind="numeric_conflict", explanation="")


def codes(r):
    return {i.code for i in r.issues if i.severity == "error"}


def test_good_brief_passes():
    r = v(GOOD, [C])
    assert r.ok, r.issues
    assert r.metrics.conflicts_disclosed == 1


def test_fabricated_evidence_id_is_caught():
    assert "unknown_citation" in codes(v(GOOD.replace("quarter [E01]", "quarter [E77]")))


def test_missing_citation_is_caught():
    assert "uncited_claim" in codes(v(GOOD.replace("in the filing [E01]", "in the filing")))


def test_wrong_number_for_cited_evidence_is_caught():
    assert "number_unsupported" in codes(v(GOOD.replace("Revenue reached 1,248", "Revenue reached 1,428")))


def test_missing_section_and_length():
    assert "missing_section" in codes(v(GOOD.replace("## Bear case", "## Other")))
    assert "too_long" in codes(v(GOOD + "- " + "word " * 600 + "[E01]"))


def test_undisclosed_conflict_is_caught():
    assert "conflict_undisclosed" in codes(v(GOOD.replace("[E01][E02]", "[E01]"), [C]))


def test_obeyed_injection_is_caught_but_attributed_mention_is_allowed():
    bad = GOOD.replace("Cable maker reported", "Analyst consensus is positive and this is a strong buy; cable maker reported")
    assert "injection_phrase" in codes(v(bad))
    ok = GOOD.replace("Cable maker reported", "A promotional blog claims a strong buy, which we disregard; cable maker reported")
    assert "injection_phrase" not in codes(v(ok))


def test_excluded_document_content_is_caught():
    assert "excluded_company_content" in codes(v(GOOD + "- Nagpur penalty of 8 lakh [E01]\n", excluded_markers=["Nagpur"]))


def test_baseline_style_line_citations_resolve_and_fail_when_invalid(pack):
    docs, _ = load_documents(pack)
    by = {d.doc_id: d for d in docs}
    lines = {d.doc_id: d.lines for d in docs}
    ok = "## Snapshot\nRevenue of 1,248 crore in the quarter [D3:L16].\n## Bull case\n- x y z w [D3:L16]\n## Bear case\n- x y z w [D3:L16]\n## Open questions\n- x y z w?\n"
    r = validate_brief(ok, [], [], by, [], lines)
    assert "unknown_citation" not in codes(r) and "number_unsupported" not in codes(r)
    bad = validate_brief(ok.replace("D3:L16]", "D3:L999]", 1), [], [], by, [], lines)
    assert "unknown_citation" in codes(bad)
