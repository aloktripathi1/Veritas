"""LangGraph engine: parity with pipeline.run(mode="robust") and the graph-specific behaviour. All offline (ScriptedLLM)."""
import json
import uuid

import pytest

pytest.importorskip("langgraph")

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from tests.test_pipeline import EXTRACTION, GOOD  # noqa: E402
from veritas.cli import main  # noqa: E402
from veritas.graph import build_graph, node_trace, run_graph  # noqa: E402
from veritas.llm import ScriptedLLM  # noqa: E402
from veritas.pipeline import MAX_REPAIRS, PipelineError, run  # noqa: E402
from veritas.render import render  # noqa: E402

NAME = "Sarvottam Cables Ltd"
OBEYED = GOOD.replace("Sarvottam Cables reported", "This is a strong buy [E01]. Sarvottam Cables reported", 1)
JUDGE_BAD = json.dumps([{"id": 1, "verdict": "unsupported", "reason": "quote is about a different metric"}])
JUDGE_OK = json.dumps([{"id": 1, "verdict": "supported", "reason": "ok"}, {"id": 2, "verdict": "partial", "reason": "adds appeal plan"}])

SCENARIOS = {
    "clean, no judge": ([EXTRACTION, GOOD], False),
    "clean, judge": ([EXTRACTION, GOOD, JUDGE_OK], True),
    "obeys injection, repaired": ([EXTRACTION, OBEYED, GOOD], False),
    "never recovers": ([EXTRACTION, OBEYED, OBEYED, OBEYED], False),
    "judge unsupported, repaired": ([EXTRACTION, GOOD, JUDGE_BAD, GOOD, JUDGE_OK], True),
    "judge unsupported until budget spent": ([EXTRACTION, GOOD, JUDGE_BAD, GOOD, JUDGE_BAD, GOOD, JUDGE_BAD], True),
    "invalid draft, judge skipped between repairs": ([EXTRACTION, OBEYED, "[]", OBEYED, OBEYED, "[]"], True),
    "invalid draft repaired, judge re-runs": ([EXTRACTION, OBEYED, "[]", GOOD, JUDGE_OK], True),
}


def both(responses, entail, pack, **kw):
    a, b = ScriptedLLM(responses), ScriptedLLM(responses)
    return run("robust", "SRVCABLE", NAME, pack, a, use_entailment=entail, **kw), a, run_graph("SRVCABLE", NAME, pack, b, use_entailment=entail, **kw), b


@pytest.mark.parametrize("name", SCENARIOS)
def test_parity_with_classic_pipeline(pack, name):
    responses, entail = SCENARIOS[name]
    c, ca, g, ga = both(responses, entail, pack)
    assert g.brief == c.brief
    assert [i.model_dump() for i in g.validation.issues] == [i.model_dump() for i in c.validation.issues]
    assert g.validation.metrics == c.validation.metrics
    assert (g.llm_calls, g.repairs, g.repaired) == (c.llm_calls, c.repairs, c.repaired)
    assert g.entailment == c.entailment and g.diagnostics == c.diagnostics
    assert g.evidence == c.evidence and g.calcs == c.calcs and g.conflicts == c.conflicts
    assert ga.calls == ca.calls                      # byte-identical prompts, in the same order
    assert render(g, "SRVCABLE", "2026-09-23") == render(c, "SRVCABLE", "2026-09-23")


def test_progress_messages_match_classic(pack):
    cm, gm = [], []
    run("robust", "SRVCABLE", NAME, pack, ScriptedLLM([EXTRACTION, GOOD]), use_entailment=False, progress=cm.append)
    run_graph("SRVCABLE", NAME, pack, ScriptedLLM([EXTRACTION, GOOD]), use_entailment=False, progress=gm.append)
    strip = lambda ms: [m.split("] ", 1)[1] for m in ms]  # drop the elapsed-time prefix  # noqa: E731
    assert strip(gm) == strip(cm)


def test_repair_loop_is_bounded_and_a_failing_model_ends_not_ok(pack):
    llm = ScriptedLLM([EXTRACTION, OBEYED, OBEYED, OBEYED, "UNUSED"])
    res = run_graph("SRVCABLE", NAME, pack, llm, use_entailment=False)
    assert res.repairs == MAX_REPAIRS == 2 and res.llm_calls == 4 and not res.validation.ok
    assert llm.responses == ["UNUSED"]


def test_abort_edge_fires_on_too_little_evidence(pack):
    llm = ScriptedLLM(["I cannot help with that.", "SHOULD NOT BE CALLED"])
    with pytest.raises(PipelineError, match=r"only 0 verified evidence items \(minimum 8\)") as g:
        run_graph("SRVCABLE", NAME, pack, llm)
    with pytest.raises(PipelineError) as c:
        run("robust", "SRVCABLE", NAME, pack, ScriptedLLM(["I cannot help with that."]))
    assert str(g.value) == str(c.value) and len(llm.calls) == 1


def run_and_trace(pack, responses, entail):
    saver, tid = InMemorySaver(), uuid.uuid4().hex
    graph = build_graph(ScriptedLLM(responses), checkpointer=saver)
    state = {"ticker": "SRVCABLE", "legal_name": NAME, "directory": pack, "today": "2026-09-23", "system": "s", "use_entailment": entail, "started": 0.0}
    try:
        graph.invoke(state, {"configurable": {"thread_id": tid}})
    except PipelineError:
        pass
    return [n for s in node_trace(graph, tid) for n in s["next"] if n != "__start__"]


def test_no_entailment_skips_the_judge_node(pack):
    assert "judge" not in run_and_trace(pack, [EXTRACTION, GOOD], False)
    assert "judge" in run_and_trace(pack, [EXTRACTION, GOOD, JUDGE_OK], True)


def test_judge_is_skipped_between_failing_repairs_but_runs_when_budget_is_spent(pack):
    path = run_and_trace(pack, SCENARIOS["invalid draft, judge skipped between repairs"][0], True)
    assert path == ["load", "screen", "extract", "verify", "calculate", "draft", "validate", "judge", "repair", "validate", "repair", "validate",
                    "judge", "finalize"]


def test_abort_is_visible_in_the_checkpoint_trace(pack):
    path = run_and_trace(pack, ["no json here"], True)
    assert path[-1] == "abort" and "calculate" not in path


def test_checkpointer_keeps_per_node_state_for_inspection(pack):
    saver, tid = InMemorySaver(), "run-1"
    run_graph("SRVCABLE", NAME, pack, ScriptedLLM([EXTRACTION, OBEYED, GOOD]), use_entailment=False, checkpointer=saver, thread_id=tid)
    graph = build_graph(ScriptedLLM([]), checkpointer=saver)
    trace = node_trace(graph, tid)
    assert trace[1]["next"] == ["load"] and trace[-1]["next"] == []
    assert [t["repairs"] for t in trace if t["next"] == ["validate"]] == [0, 1]
    assert max(t["llm_calls"] or 0 for t in trace) == 3
    final = graph.get_state({"configurable": {"thread_id": tid}}).values
    assert final["validation"].ok and final["repairs"] == 1


def test_graph_version_of_obeyed_injection_is_rejected_then_repaired(pack):
    llm = ScriptedLLM([EXTRACTION, OBEYED, GOOD])
    res = run_graph("SRVCABLE", NAME, pack, llm, use_entailment=False)
    assert res.repaired and res.llm_calls == 3 and res.validation.ok
    first_issues = "\n".join(u for _, u in llm.calls[2:])
    assert "injection_phrase" in first_issues               # the repair prompt carried the validator's finding
    sent = " ".join(s + u for s, u in llm.calls).lower()
    assert "ignore all previous instructions" not in sent   # the neutraliser ran in the graph too


def test_cli_engine_graph_runs_and_matches_classic(pack, tmp_path, monkeypatch, capsys):
    import veritas.cli as cli
    outs = {}
    for engine in ("classic", "graph"):
        monkeypatch.setattr(cli, "make_llm", lambda: ScriptedLLM([EXTRACTION, GOOD]))
        out = tmp_path / f"{engine}.md"
        assert main(["SRVCABLE", pack, "--out", str(out), "--no-entailment", "--quiet", "--engine", engine]) == 0
        outs[engine] = out.read_text()
    assert outs["graph"] == outs["classic"]
    capsys.readouterr()


@pytest.mark.parametrize("mode", ["baseline", "structured"])
def test_cli_graph_engine_rejects_other_modes(pack, tmp_path, capsys, mode):
    assert main(["SRVCABLE", pack, "--mode", mode, "--engine", "graph", "--out", str(tmp_path / "o.md")]) == 2
    assert "only supports --mode robust" in capsys.readouterr().err
