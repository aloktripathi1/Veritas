"""LangGraph engine for the robust pipeline.

Same steps, same helpers and same Result as `pipeline.run(mode="robust")`; only the control flow is different:
each stage is a node over an explicit typed state, the two branch points (abort on too little evidence, and the
bounded judge/repair loop) are conditional edges, and a checkpointer records the state after every node.
Requires the optional `graph` extra (`pip install .[graph]`).
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from . import entail
from .evidence import Calc, build_registry, parse_claims
from .identity import exclusion_markers
from .llm import LLM
from .loader import load_documents
from .models import Conflict, Document, Evidence
from .pipeline import (MAX_REPAIRS, MIN_EVIDENCE, PipelineError, Result, _strip_fence, calculate, draft_user_message, prompt, record_entailment,
                       redaction_evidence, registry_context, repair_user_message, robust_extra, screen, screen_diagnostics, wrap_docs)
from .validate import ValidationResult, validate_brief


class VeritasState(TypedDict, total=False):
    # inputs
    ticker: str
    legal_name: str
    directory: str
    today: str
    system: str
    use_entailment: bool
    started: float
    # load / screen
    docs: list[Document]
    lines_by_doc: dict[str, list[str]]
    allowed: list[str]
    excluded: list[str]
    markers: list[str]
    # extract / verify / calculate
    raw_extraction: str
    evidence: list[Evidence]
    calcs: list[Calc]
    conflicts: list[Conflict]
    redaction_ids: dict[str, str]
    context: str
    # draft / validate / judge / repair
    brief: str
    validation: ValidationResult
    verdicts: list[entail.Verdict]
    index: dict[int, str]
    entailment: list[dict]
    repairs: int
    llm_calls: int
    diagnostics: list[str]


NODES = ("load", "screen", "extract", "verify", "calculate", "draft", "validate", "judge", "repair", "finalize", "abort")


def route_after_verify(state: VeritasState) -> str:
    return "abort" if len(state["evidence"]) < MIN_EVIDENCE else "calculate"


def route_after_validate(state: VeritasState) -> str:
    """The advisory judge runs on the first draft, and after a repair only when it is clean or the budget is spent; it is skippable."""
    due = state["repairs"] == 0 or state["validation"].ok or state["repairs"] == MAX_REPAIRS
    return "judge" if state["use_entailment"] and due else route_after_judge(state)


def route_after_judge(state: VeritasState) -> str:
    """Mirror of the loop in pipeline.run: stop when clean, otherwise repair while the budget lasts."""
    bad = any(v.verdict == "unsupported" for v in state["verdicts"])
    if state["validation"].ok and not bad:
        return "finalize"
    return "repair" if state["repairs"] < MAX_REPAIRS else "finalize"


def build_graph(llm: LLM, progress: Callable[[str], None] | None = None, checkpointer: BaseCheckpointSaver | None = None):
    """Compile the robust-mode graph. `llm` and `progress` are bound here; everything else flows through state."""

    def say(state: VeritasState, msg: str) -> None:
        if progress:
            progress(f"[{time.time() - state['started']:5.1f}s] {msg}")

    def ids(state: VeritasState) -> tuple[dict[str, Document], set[str], set[str]]:
        return {d.doc_id: d for d in state["docs"]}, set(state["allowed"]), set(state["excluded"])

    def validate_now(state: VeritasState, brief: str) -> ValidationResult:
        by_id, _, excluded = ids(state)
        return validate_brief(brief, state["evidence"], state["calcs"], by_id, state["conflicts"], state["lines_by_doc"], excluded, state["markers"])

    def load(state: VeritasState) -> dict[str, Any]:
        docs, diags = load_documents(state["directory"])
        say(state, f"[1/6] Read {len(docs)} documents from {state['directory']}")
        return {"docs": docs, "diagnostics": diags, "repairs": 0, "llm_calls": 0, "evidence": [], "calcs": [], "conflicts": [],
                "verdicts": [], "index": {}}

    def screen_node(state: VeritasState) -> dict[str, Any]:
        docs = [d.model_copy(deep=True) for d in state["docs"]]   # screening sets relevance/flags on the documents
        lines_by_doc = screen(docs, state["ticker"], state["legal_name"])
        for d in docs:
            status = "relevant" if d.relevant else "EXCLUDED (different company)"
            flag = "  | instruction-like text redacted" if d.injection_flags else ""
            say(state, f"      {d.doc_id} {d.filename:<32} {d.published}  {status}{flag}")
        rel, exc = [d for d in docs if d.relevant], [d for d in docs if not d.relevant]
        return {"docs": docs, "lines_by_doc": lines_by_doc, "diagnostics": state["diagnostics"] + screen_diagnostics(docs),
                "allowed": [d.doc_id for d in rel], "excluded": [d.doc_id for d in exc],
                "markers": exclusion_markers(exc, rel) if exc else []}

    def extract(state: VeritasState) -> dict[str, Any]:
        _, allowed, _ = ids(state)
        user = prompt("extract").format(ticker=state["ticker"], legal_name=state["legal_name"]) + "\n\n" + wrap_docs(state["docs"], state["lines_by_doc"], allowed)
        say(state, "[2/6] Extracting claims with verbatim quotes (LLM call 1) ...")
        return {"raw_extraction": llm.complete(state["system"], user), "llm_calls": state["llm_calls"] + 1}

    def verify(state: VeritasState) -> dict[str, Any]:
        by_id, allowed, _ = ids(state)
        claims, d1 = parse_claims(state["raw_extraction"])
        evidence, d2 = build_registry(claims, by_id, state["lines_by_doc"], allowed)
        say(state, f"      {len(claims)} claims proposed; {len(evidence)} verified verbatim against the source text, {len(claims) - len(evidence)} rejected")
        return {"evidence": evidence, "diagnostics": state["diagnostics"] + d1 + d2}

    def abort(state: VeritasState) -> dict[str, Any]:
        raise PipelineError(f"extraction produced only {len(state['evidence'])} verified evidence items (minimum {MIN_EVIDENCE}); "
                            f"aborting before synthesis. Diagnostics: {state['diagnostics'][-3:]}")

    def calculate_node(state: VeritasState) -> dict[str, Any]:
        by_id, _, _ = ids(state)
        evidence = list(state["evidence"])
        redaction_ids = redaction_evidence(state["docs"], state["lines_by_doc"], evidence)
        calcs, conflicts = calculate(evidence, state["docs"], state["lines_by_doc"], by_id)
        say(state, f"[3/6] Python calculations: {len(calcs)}; conflicts between sources: {len(conflicts)}")
        for c in conflicts:
            say(state, f"      ! {c.kind}: {c.metric} = {' vs '.join(f'{v:g}' for v in c.values)}")
        return {"evidence": evidence, "calcs": calcs, "conflicts": conflicts, "redaction_ids": redaction_ids}

    def draft(state: VeritasState) -> dict[str, Any]:
        by_id, allowed, excluded = ids(state)
        extra = robust_extra(state["docs"], excluded, state["redaction_ids"])
        context = registry_context(state["evidence"], state["calcs"], state["conflicts"], by_id, allowed)
        say(state, "[4/6] Writing the brief from the evidence registry (LLM call 2) ...")
        brief = _strip_fence(llm.complete(state["system"], draft_user_message(state["ticker"], state["legal_name"], state["today"], extra, context)))
        return {"context": context, "brief": brief, "llm_calls": state["llm_calls"] + 1}

    def validate(state: VeritasState) -> dict[str, Any]:
        validation = validate_now(state, state["brief"])
        if state["repairs"] == 0:
            say(state, f"[5/6] Validating citations, numbers and conflict disclosure: {sum(i.severity == 'error' for i in validation.issues)} error(s)")
        return {"validation": validation, "verdicts": [], "index": {}}   # verdicts belong to the previous draft; the judge refreshes them

    def judge(state: VeritasState) -> dict[str, Any]:
        first = state["repairs"] == 0
        n = 0

        def call(sys_: str, user: str) -> str:
            nonlocal n
            n += 1
            return llm.complete(sys_, user)

        if first:
            say(state, "[6/6] Advisory check that each cited quote supports its sentence (LLM call 3) ...")
        verdicts, index, diags = entail.judge(call, state["system"], prompt("entail"), state["brief"], state["evidence"], state["calcs"])
        if first and verdicts:
            say(state, "      " + ", ".join(f"{c} {k}" for k in ("supported", "partial", "unsupported") if (c := sum(v.verdict == k for v in verdicts))))
        return {"verdicts": verdicts, "index": index, "llm_calls": state["llm_calls"] + n, "diagnostics": state["diagnostics"] + diags}

    def repair(state: VeritasState) -> dict[str, Any]:
        user = repair_user_message(state["validation"], state["verdicts"], state["index"], state["brief"], state["context"])
        brief = _strip_fence(llm.complete(state["system"], user))
        say(state, f"      repair pass {state['repairs'] + 1}: fixing the problems found above ...")
        return {"brief": brief, "repairs": state["repairs"] + 1, "llm_calls": state["llm_calls"] + 1}

    def finalize(state: VeritasState) -> dict[str, Any]:
        res = Result(mode="robust", brief=state["brief"], docs=state["docs"], validation=state["validation"].model_copy(deep=True))
        record_entailment(res, state["verdicts"], state["index"])
        return {"validation": res.validation, "entailment": res.entailment}

    g = StateGraph(VeritasState)
    for name, fn in (("load", load), ("screen", screen_node), ("extract", extract), ("verify", verify), ("calculate", calculate_node), ("draft", draft),
                     ("validate", validate), ("judge", judge), ("repair", repair), ("finalize", finalize), ("abort", abort)):
        g.add_node(name, fn)
    g.add_edge(START, "load")
    g.add_edge("load", "screen")
    g.add_edge("screen", "extract")
    g.add_edge("extract", "verify")
    g.add_conditional_edges("verify", route_after_verify, {"calculate": "calculate", "abort": "abort"})
    g.add_edge("calculate", "draft")
    g.add_edge("draft", "validate")
    g.add_conditional_edges("validate", route_after_validate, {"judge": "judge", "repair": "repair", "finalize": "finalize"})
    g.add_conditional_edges("judge", route_after_judge, {"repair": "repair", "finalize": "finalize"})
    g.add_edge("repair", "validate")
    g.add_edge("finalize", END)
    g.add_edge("abort", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def run_graph_state(ticker: str, legal_name: str, directory: str, llm: LLM, today: str = "2026-09-23", system_prompt: str | None = None,
                    use_entailment: bool = True, progress: Callable[[str], None] | None = None, *,
                    checkpointer: BaseCheckpointSaver | None = None, thread_id: str | None = None) -> VeritasState:
    """Run the graph and return the final state (documents, line maps, registry, brief, validation, ...)."""
    graph = build_graph(llm, progress, checkpointer)
    system = Path(system_prompt).read_text(encoding="utf-8") if system_prompt else prompt("system")
    config = {"configurable": {"thread_id": thread_id or uuid.uuid4().hex}}
    return graph.invoke({"ticker": ticker, "legal_name": legal_name, "directory": directory, "today": today, "system": system,
                         "use_entailment": use_entailment, "started": time.time()}, config)


def to_result(s: VeritasState, llm: LLM) -> Result:
    return Result(mode="robust", brief=s["brief"], docs=s["docs"], evidence=s["evidence"], calcs=s["calcs"], conflicts=s["conflicts"],
                  validation=s["validation"], diagnostics=s["diagnostics"], llm_calls=s["llm_calls"], seconds=time.time() - s["started"],
                  repaired=s["repairs"] > 0, repairs=s["repairs"], raw_extraction=s["raw_extraction"], entailment=s["entailment"],
                  usage=dict(getattr(llm, "usage", {}) or {}))


def run_graph(ticker: str, legal_name: str, directory: str, llm: LLM, today: str = "2026-09-23", system_prompt: str | None = None,
              use_entailment: bool = True, progress: Callable[[str], None] | None = None, *,
              checkpointer: BaseCheckpointSaver | None = None, thread_id: str | None = None) -> Result:
    """Same inputs and Result as `pipeline.run("robust", ...)`. Pass `thread_id` (and keep the checkpointer) to inspect per-node state afterwards."""
    return to_result(run_graph_state(ticker, legal_name, directory, llm, today, system_prompt, use_entailment, progress,
                                     checkpointer=checkpointer, thread_id=thread_id), llm)


def node_trace(graph, thread_id: str) -> list[dict[str, Any]]:
    """Checkpoint history of a run, oldest first: the step, the node that ran next, and a few state facts."""
    snaps = list(graph.get_state_history({"configurable": {"thread_id": thread_id}}))[::-1]
    return [{"step": s.metadata.get("step"), "next": list(s.next), "repairs": s.values.get("repairs"), "llm_calls": s.values.get("llm_calls"),
             "evidence": len(s.values.get("evidence", []))} for s in snaps]
