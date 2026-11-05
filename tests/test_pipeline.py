"""Integration tests with a scripted (offline) model. These test OUR pipeline, not model quality;
live-model behaviour is covered by tests/test_live.py and docs/evaluation.md."""
from veritas.cli import main
from veritas.llm import ScriptedLLM
from veritas.pipeline import run
from veritas.render import render
from veritas.sim import EXTRACTION, GOOD  # other test modules import these two from here


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
