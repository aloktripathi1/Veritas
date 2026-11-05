"""`veritas-eval`: repeated-run statistics and LLM-judge agreement.

repeat  Run a scenario N times and report pass rate (with a Wilson interval), mean/std of repairs, calls, seconds, words and evidence,
        run-to-run consistency of the cited evidence, and the failure cases grouped by validator error code.
        Scenarios are *simulated* (a seeded stochastic stand-in for the model) or --live (the real model on the real corpus).
        Simulated results test this harness and the pipeline's handling of failures; they say nothing about model quality.
judge   Measure the advisory entailment judge against labelled cases built from a stored run: every cited sentence of the brief is kept as is
        (label: not unsupported) and also rewritten to cite an unrelated evidence item (label: unsupported by construction). Reports the judge's
        false-positive and detection rates, the deterministic validator's detection rate on the same cases, and their agreement (Cohen's kappa).
        --judge live uses the real model; --judge number|lazy are rule-based stand-ins for testing the harness.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Callable

from . import entail
from .evidence import Calc
from .llm import LLM, LLMError, make_llm
from .loader import LoadError
from .models import Conflict, Evidence
from .pipeline import DEFAULT_TODAY, PipelineError, prompt, run, validate_against_registry
from .sim import EXTRACTION, GOOD
from .validate import CITE, NUM, claim_sentences

TICKER, COMPANY = "SRVCABLE", "Sarvottam Cables Ltd"
BAD = GOOD.replace("Sarvottam Cables reported", "This is a strong buy [E01]. Sarvottam Cables reported", 1)
SIMULATED = {"clean": 0.0, "flaky": 0.5, "stubborn": 1.0}   # probability that a drafting/repair call violates the recommendation rule


# ---------------------------------------------------------------- repeated runs
class StochasticLLM:
    """Simulation, not a model: valid extraction, then each drafting/repair call is bad with probability p (seeded)."""
    name = "simulated"

    def __init__(self, p_violation: float, seed: int):
        self.p, self.rng, self.n = p_violation, random.Random(seed), 0

    def complete(self, system: str, user: str) -> str:
        self.n += 1
        return EXTRACTION if self.n == 1 else (BAD if self.rng.random() < self.p else GOOD)


@dataclass
class RunStat:
    passed: bool = False
    repairs: int = 0
    llm_calls: int = 0
    seconds: float = 0.0
    words: int = 0
    evidence: int = 0
    error_codes: list[str] | None = None
    cited: list[str] | None = None
    failure: str | None = None        # run raised (load / LLM / pipeline error) instead of finishing


def repeat(n: int, make_model: Callable[[int], LLM], directory: str, engine: str = "classic", use_entailment: bool = False) -> list[RunStat]:
    out = []
    for i in range(n):
        model = make_model(i)
        try:
            if engine == "graph":
                from .graph import run_graph
                res = run_graph(TICKER, COMPANY, directory, model, DEFAULT_TODAY, None, use_entailment)
            else:
                res = run("robust", TICKER, COMPANY, directory, model, DEFAULT_TODAY, None, use_entailment)
        except (LoadError, LLMError, PipelineError) as e:
            out.append(RunStat(failure=f"{type(e).__name__}: {e}"[:200]))
            continue
        errs = [x.code for x in res.validation.issues if x.severity == "error"]
        out.append(RunStat(res.validation.ok, res.repairs, res.llm_calls, res.seconds, res.validation.metrics.body_words, len(res.evidence), errs,
                           sorted(set(CITE.findall(res.brief)))))
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)


def summarize(stats: list[RunStat]) -> dict:
    n = len(stats)
    done = [s for s in stats if s.failure is None]
    k = sum(s.passed for s in done)

    def agg(attr: str) -> dict | None:
        xs = [getattr(s, attr) for s in done]
        return {"mean": round(statistics.fmean(xs), 2), "std": round(statistics.stdev(xs), 2) if len(xs) > 1 else None} if xs else None

    sets = [set(s.cited) for s in done]
    pairs = [len(a & b) / len(a | b) for a, b in combinations(sets, 2) if a | b]
    return {"runs": n, "finished": len(done), "errored": n - len(done), "passed": k, "pass_rate": round(k / n, 3) if n else None, "pass_rate_95ci": wilson(k, n),
            "repairs": agg("repairs"), "llm_calls": agg("llm_calls"), "seconds": agg("seconds"), "body_words": agg("words"), "evidence": agg("evidence"),
            "cited_evidence_consistency": round(statistics.fmean(pairs), 3) if pairs else None,
            "failure_codes": dict(Counter(c for s in done if not s.passed for c in set(s.error_codes or []))),
            "errors": dict(Counter(s.failure for s in stats if s.failure))}


# ---------------------------------------------------------------- judge agreement
def load_run(run_dir: str | Path) -> tuple[str, list[Evidence], list[Calc], list[Conflict]]:
    d = Path(run_dir)
    reg = json.loads((d / "evidence.json").read_text(encoding="utf-8"))
    md = (d / "output.md").read_text(encoding="utf-8")
    body = md.split("\n## Sources")[0].split("\n\n", 1)[1].strip()          # drop the "# ... research brief" title line
    return body, [Evidence(**e) for e in reg["evidence"]], [Calc(**c) for c in reg["calculations"]], [Conflict(**c) for c in reg["conflicts"]]


def rewrite_with_wrong_citation(sentence: str, ev: list[Evidence], offset: int) -> str | None:
    """The same sentence citing one evidence item that has a different metric and shares no number with it (so it cannot support it)."""
    ids = CITE.findall(sentence)
    cited = {e.metric for e in ev if e.evidence_id in ids}
    nums = set(NUM.findall(sentence))
    pool = [e for e in ev if e.metric not in cited and e.evidence_id not in ids and not any(n in e.quote for n in nums)]
    if not pool:
        return None
    other = pool[offset % len(pool)]
    text = re.sub(r"\s*" + CITE.pattern, "", sentence).rstrip()
    return text[:-1] + f" [{other.evidence_id}]" + text[-1] if text[-1:] in ".!?" else f"{text} [{other.evidence_id}]"


def judge_agreement(run_dir: str | Path, judge_llm: LLM, directory: str) -> dict:
    body, ev, calcs, conflicts = load_run(run_dir)
    ids = {e.evidence_id for e in ev}
    cases = []
    for i, (_, sent) in enumerate(claim_sentences(body)):
        if sent in body and set(CITE.findall(sent)) & ids:
            wrong = rewrite_with_wrong_citation(sent, ev, i)
            if wrong:
                cases.append({"sentence": sent, "mutated": wrong})
    if not cases:
        raise ValueError("no cited sentences could be turned into test cases")

    def errors(brief: str) -> int:
        return sum(i.severity == "error" for i in validate_against_registry(brief, TICKER, COMPANY, directory, ev, calcs, conflicts).issues)

    base = errors(body)
    mutated_brief = body
    for c in cases:
        mutated_brief = mutated_brief.replace(c["sentence"], c["mutated"], 1)
        c["validator_flagged"] = errors(body.replace(c["sentence"], c["mutated"], 1)) > base

    def verdicts(brief: str) -> dict[str, tuple[str, str]]:
        vs, index, diags = entail.judge(judge_llm.complete, prompt("system"), prompt("entail"), brief, ev, calcs)
        if not vs and diags:
            raise RuntimeError(f"judge output unusable: {diags}")
        return {index[v.id]: (v.verdict, v.reason) for v in vs if v.id in index}

    orig, mut = verdicts(body), verdicts(mutated_brief)
    for c in cases:
        c["judge_original"], c["judge_mutated"] = orig.get(c["sentence"], ("missing", "")), mut.get(c["mutated"], ("missing", ""))
    scored = [c for c in cases if c["judge_original"][0] != "missing" and c["judge_mutated"][0] != "missing"]
    m = len(scored)
    fp = sum(c["judge_original"][0] == "unsupported" for c in scored)
    tp = sum(c["judge_mutated"][0] == "unsupported" for c in scored)
    v_tp = sum(c["validator_flagged"] for c in scored)
    both = sum(c["validator_flagged"] and c["judge_mutated"][0] == "unsupported" for c in scored)
    # agreement of judge flag vs validator flag over originals (validator flags none: base is clean) and mutations
    j_flags = [c["judge_original"][0] == "unsupported" for c in scored] + [c["judge_mutated"][0] == "unsupported" for c in scored]
    v_flags = [False] * m + [c["validator_flagged"] for c in scored]
    return {"cases": m, "unscored": len(cases) - m, "baseline_validator_errors": base,
            "judge": {"false_positive_rate_on_originals": round(fp / m, 3) if m else None, "partial_on_originals": sum(c["judge_original"][0] == "partial" for c in scored),
                      "detection_rate_on_mutations": round(tp / m, 3) if m else None, "detection_95ci": wilson(tp, m),
                      "accuracy": round(((m - fp) + tp) / (2 * m), 3) if m else None},
            "validator": {"detection_rate_on_mutations": round(v_tp / m, 3) if m else None, "detection_95ci": wilson(v_tp, m)},
            "agreement": {"both_flag": both, "judge_only": tp - both, "validator_only": v_tp - both, "neither": m - tp - v_tp + both,
                          "cohen_kappa": kappa(j_flags, v_flags)},
            "judge_missed": [{"mutated_sentence": c["mutated"], "judge": c["judge_mutated"][0], "validator_flagged": c["validator_flagged"]}
                             for c in scored if c["judge_mutated"][0] != "unsupported"],
            "judge_false_alarms": [{"sentence": c["sentence"], "reason": c["judge_original"][1]} for c in scored if c["judge_original"][0] == "unsupported"]}


def kappa(a: list[bool], b: list[bool]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = (sum(a) / n) * (sum(b) / n) + (1 - sum(a) / n) * (1 - sum(b) / n)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


class RuleJudge:
    """Rule-based stand-ins for the LLM judge, to test the harness. 'number': unsupported unless every number in the sentence is in a cited
    text. 'lazy': says supported to everything (a useless judge: the harness must show it detecting nothing)."""

    def __init__(self, kind: str):
        self.kind, self.name = kind, f"rule-judge:{kind}"

    def complete(self, system: str, user: str) -> str:
        out = []
        for item in json.loads(user.rsplit("\n\n", 1)[-1]):
            texts = " ".join(c["text"] for c in item["cited"])
            ok = self.kind == "lazy" or all(n in texts for n in NUM.findall(item["sentence"]))
            out.append({"id": item["id"], "verdict": "supported" if ok else "unsupported", "reason": "rule"})
        return json.dumps(out)


# ---------------------------------------------------------------- reports / CLI
def fmt(x) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, dict) and "mean" in x:
        return f"{x['mean']} (std {x['std'] if x['std'] is not None else 'n/a'})"
    return str(x)


def repeat_report(title: str, source: str, s: dict) -> str:
    ci = s["pass_rate_95ci"]
    lines = [f"# Repeated runs: {title}", "", f"Source: **{source}**. Pass = deterministic validation ok. Errored runs count as not passing.", "",
             "| metric | value |", "|---|---|", f"| runs (finished / errored) | {s['runs']} ({s['finished']} / {s['errored']}) |",
             f"| pass rate | {s['passed']}/{s['runs']} = {fmt(s['pass_rate'])}, 95% Wilson interval {f'{ci[0]} to {ci[1]}' if ci else 'n/a'} |",
             f"| repairs, mean (std) | {fmt(s['repairs'])} |", f"| LLM calls | {fmt(s['llm_calls'])} |", f"| seconds | {fmt(s['seconds'])} |",
             f"| body words | {fmt(s['body_words'])} |", f"| evidence items | {fmt(s['evidence'])} |",
             f"| cited-evidence consistency (mean pairwise Jaccard) | {fmt(s['cited_evidence_consistency'])} |", "", "## Failure cases"]
    if s["failure_codes"]:
        lines += [f"- `{c}`: in {n} failed run(s)" for c, n in sorted(s["failure_codes"].items(), key=lambda kv: -kv[1])]
    if s["errors"]:
        lines += [f"- run error ({n}x): {m}" for m, n in s["errors"].items()]
    if not s["failure_codes"] and not s["errors"]:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def judge_report(run_dir: str, judge_name: str, r: dict) -> str:
    j, v, a = r["judge"], r["validator"], r["agreement"]
    lines = [f"# Judge agreement on `{run_dir}`", "", f"Judge under test: **{judge_name}**. {r['cases']} cited sentences, each kept as is and rewritten to cite an unrelated "
             f"evidence item ({r['unscored']} not scored). Baseline validator errors on the stored brief: {r['baseline_validator_errors']}.", "",
             "| | judge | validator |", "|---|---|---|", f"| detects the wrong-citation rewrite | {j['detection_rate_on_mutations']} (95% CI {j['detection_95ci']}) | "
             f"{v['detection_rate_on_mutations']} (95% CI {v['detection_95ci']}) |", f"| flags an original sentence as unsupported | {j['false_positive_rate_on_originals']} | 0 by construction |",
             "", f"Judge accuracy over originals and rewrites: {j['accuracy']}; judge said `partial` on {j['partial_on_originals']} originals. "
             f"Agreement on rewrites: both flag {a['both_flag']}, judge only {a['judge_only']}, validator only {a['validator_only']}, neither {a['neither']}; Cohen's kappa {a['cohen_kappa']}.",
             "", "## Rewrites the judge missed"]
    lines += [f"- ({'validator caught it' if m['validator_flagged'] else 'validator missed it too'}, judge: {m['judge']}) {m['mutated_sentence'][:160]}" for m in r["judge_missed"]] or ["- none"]
    lines += ["", "## Original sentences the judge called unsupported"] + ([f"- {m['sentence'][:140]}: {m['reason']}" for m in r["judge_false_alarms"]] or ["- none"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="veritas-eval", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("repeat", help="run a scenario N times")
    r.add_argument("--scenario", choices=sorted(SIMULATED), help="simulated model (tests the harness, says nothing about real models)")
    r.add_argument("--live", action="store_true", help="use the real model from SRV_PROVIDER/SRV_MODEL on the real corpus")
    r.add_argument("-n", type=int, default=20)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--engine", choices=("graph", "classic"), default="classic", help="graph needs the [graph] extra")
    r.add_argument("--docs", default=str(Path(__file__).resolve().parents[2] / "research_pack"))
    r.add_argument("--entailment", action="store_true", help="include the advisory judge in each run (live only)")
    j = sub.add_parser("judge", help="judge agreement on a stored run (evidence.json + output.md)")
    j.add_argument("--run", default="runs/round2/r5_a")
    j.add_argument("--judge", choices=("live", "number", "lazy"), required=True)
    j.add_argument("--docs", default=str(Path(__file__).resolve().parents[2] / "research_pack"))
    for p in (r, j):
        p.add_argument("--out", help="write the markdown report here")
        p.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "repeat":
            if bool(a.scenario) == a.live:
                ap.error("choose exactly one of --scenario NAME or --live")
            if a.live:
                llm = make_llm()
                stats, source, title = repeat(a.n, lambda i: llm, a.docs, a.engine, a.entailment), f"LIVE model {llm.name}, engine {a.engine}", f"live, n={a.n}"
            else:
                stats = repeat(a.n, lambda i: StochasticLLM(SIMULATED[a.scenario], a.seed + i), a.docs, a.engine)
                source, title = f"SIMULATED (seeded stand-in, p(violation)={SIMULATED[a.scenario]}, seed {a.seed}); not a measurement of any model", f"simulated '{a.scenario}', n={a.n}"
            s = summarize(stats)
            text = json.dumps({"source": source, **s}, indent=1, default=str) if a.json else repeat_report(title, source, s)
        else:
            judge = make_llm() if a.judge == "live" else RuleJudge(a.judge)
            res = judge_agreement(a.run, judge, a.docs)
            text = json.dumps({"judge": judge.name, **res}, indent=1, default=str) if a.json else judge_report(a.run, judge.name + ("" if a.judge == "live" else " (RULE-BASED STAND-IN, not an LLM)"), res)
    except (LoadError, LLMError, PipelineError, ValueError, FileNotFoundError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
