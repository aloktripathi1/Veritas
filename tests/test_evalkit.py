"""veritas-eval: statistics, repeated simulated runs, and judge agreement on the stored run runs/round2/r5_a. Offline and deterministic."""
import json
from pathlib import Path

import importlib.util

import pytest

from veritas.evalkit import (RuleJudge, RunStat, StochasticLLM, judge_agreement, kappa, load_run, main, repeat, rewrite_with_wrong_citation, summarize,
                             wilson)

GRAPH = pytest.param("graph", marks=pytest.mark.skipif(importlib.util.find_spec("langgraph") is None, reason="needs the graph extra"))
STORED = str(Path(__file__).resolve().parents[1] / "runs" / "round2" / "r5_a")


def test_wilson_interval_known_values():
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0
    lo, hi = wilson(5, 10)
    assert (lo, hi) == (0.237, 0.763) and wilson(0, 0) is None


def test_kappa_known_values():
    assert kappa([True, True, False, False], [True, True, False, False]) == 1.0
    assert kappa([True, False, True, False], [True, True, False, False]) == 0.0
    assert kappa([True, False], [False, True]) == -1.0 and kappa([], []) is None


def test_summarize_hand_built_stats():
    stats = [RunStat(True, 0, 2, 1.0, 400, 13, [], ["E01", "E02"]), RunStat(False, 2, 4, 3.0, 420, 13, ["injection_phrase", "uncited_claim"], ["E01", "E03"]),
             RunStat(failure="PipelineError: x")]
    s = summarize(stats)
    assert (s["runs"], s["finished"], s["errored"], s["passed"], s["pass_rate"]) == (3, 2, 1, 1, 0.333)
    assert s["repairs"] == {"mean": 1.0, "std": 1.41} and s["seconds"]["mean"] == 2.0
    assert s["cited_evidence_consistency"] == 0.333                      # {E01,E02} vs {E01,E03}: 1 shared of 3
    assert s["failure_codes"] == {"injection_phrase": 1, "uncited_claim": 1} and s["errors"] == {"PipelineError: x": 1}


@pytest.mark.parametrize("engine", [GRAPH, "classic"])
def test_simulated_scenarios_behave_as_designed(pack, engine):
    clean = summarize(repeat(5, lambda i: StochasticLLM(0.0, i), pack, engine))
    assert clean["pass_rate"] == 1.0 and clean["repairs"]["mean"] == 0 and clean["cited_evidence_consistency"] == 1.0 and clean["failure_codes"] == {}
    stubborn = summarize(repeat(5, lambda i: StochasticLLM(1.0, i), pack, engine))
    assert stubborn["passed"] == 0 and stubborn["repairs"]["mean"] == 2 and stubborn["failure_codes"]["injection_phrase"] == 5
    assert stubborn["pass_rate_95ci"][1] < 0.5


def test_flaky_scenario_is_seeded_reproducible_and_engines_agree(pack):
    pytest.importorskip("langgraph")
    def go(engine, seed):
        s = summarize(repeat(20, lambda i: StochasticLLM(0.5, seed + i), pack, engine))
        return {k: v for k, v in s.items() if k != "seconds"}
    a = go("graph", 0)
    assert a == go("graph", 0) == go("classic", 0)                       # same seed, same numbers, either engine
    assert 0 < a["pass_rate"] < 1 and a["repairs"]["std"] > 0 and a["failure_codes"]
    assert a != go("graph", 1000)


def test_errored_runs_count_as_not_passing(pack):
    class Down:
        name = "down"

        def complete(self, system, user):
            from veritas.llm import LLMError
            raise LLMError("offline")
    s = summarize(repeat(3, lambda i: Down(), pack, "classic"))
    assert s["errored"] == 3 and s["passed"] == 0 and s["pass_rate"] == 0.0 and list(s["errors"].values()) == [3]


def test_rewrite_keeps_punctuation_and_swaps_the_citation():
    _, ev, _, _ = load_run(STORED)
    out = rewrite_with_wrong_citation("Revenue rose 18.0% to ₹1,248 crore in Q1 FY27, per the filing [E22][E24].", ev, 0)
    assert out.endswith("].") and "[E22]" not in out and "[E24]" not in out and out.count("[E") == 1


def test_lazy_judge_is_exposed_by_the_harness(pack):
    r = judge_agreement(STORED, RuleJudge("lazy"), pack)
    assert r["cases"] == 24 and r["baseline_validator_errors"] == 0
    assert r["judge"]["detection_rate_on_mutations"] == 0.0 and r["judge"]["accuracy"] == 0.5 and r["agreement"]["cohen_kappa"] == 0.0
    assert r["validator"]["detection_rate_on_mutations"] == 0.708 and r["agreement"]["validator_only"] == 17    # the validator is deterministic: 17 of 24


def test_number_judge_agrees_with_the_validator_on_most_cases(pack):
    r = judge_agreement(STORED, RuleJudge("number"), pack)
    assert r["judge"]["detection_rate_on_mutations"] == 0.708 and r["agreement"]["both_flag"] == 17 and r["agreement"]["cohen_kappa"] > 0.8
    assert r["judge_missed"] and all(not m["validator_flagged"] for m in r["judge_missed"])    # claims without numbers slip past both


def test_cli_repeat_json_and_report(pack, tmp_path, capsys):
    out = tmp_path / "r.md"
    assert main(["repeat", "--scenario", "flaky", "-n", "6", "--docs", pack, "--out", str(out)]) == 0
    text = out.read_text()
    assert "SIMULATED" in text and "not a measurement of any model" in text and "Wilson" in text
    capsys.readouterr()
    assert main(["repeat", "--scenario", "clean", "-n", "3", "--docs", pack, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["pass_rate"] == 1.0


def test_cli_judge_report_labels_stand_ins(pack, capsys):
    assert main(["judge", "--run", STORED, "--judge", "number", "--docs", pack]) == 0
    out = capsys.readouterr().out
    assert "RULE-BASED STAND-IN, not an LLM" in out and "Rewrites the judge missed" in out


def test_cli_errors_are_clean(pack, capsys, tmp_path):
    with pytest.raises(SystemExit):
        main(["repeat", "-n", "1"])                                      # neither --scenario nor --live
    assert main(["judge", "--run", str(tmp_path), "--judge", "lazy", "--docs", pack]) == 2
    assert "ERROR" in capsys.readouterr().err
