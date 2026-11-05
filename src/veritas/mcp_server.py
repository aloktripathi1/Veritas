"""MCP server (stdio) exposing Veritas as tools: screen_documents, build_brief, get_evidence, validate_brief.

`python -m veritas.mcp_server` or the `veritas-mcp` console script. Requires the optional `mcp` extra.
Nothing is written to stdout except protocol messages; documents are only read from under VERITAS_DOCS_ROOT.
"""
from __future__ import annotations

import os
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .identity import exclusion_markers
from .llm import LLM, LLMError, make_llm
from .loader import LoadError, load_documents
from .pipeline import PipelineError, screen
from .validate import validate_brief as check_brief

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "research_pack"
MAX_RUNS = 20            # finished runs kept in memory; the oldest is dropped first
MAX_BRIEF_CHARS = 50_000


def _need(value: str, name: str) -> str:
    if not value or not value.strip():
        raise ToolError(f"{name} must not be empty")
    return value.strip()


@contextmanager
def _clean_errors() -> Iterator[None]:
    """Domain failures become tool errors (isError result) instead of crashing the server."""
    try:
        yield
    except LoadError as e:
        raise ToolError(f"Could not load documents: {e}") from e
    except LLMError as e:
        raise ToolError(f"LLM error: {e}") from e
    except PipelineError as e:
        raise ToolError(f"Pipeline error: {e}") from e


def create_server(llm: LLM | Callable[[], LLM] | None = None, docs_root: str | Path | None = None, today: str = "2026-09-23") -> FastMCP:
    """Build the server. `llm` is an LLM or a factory (default make_llm, called lazily so tools without an LLM work with no credentials)."""
    from .graph import run_graph_state, to_result

    root = Path(docs_root or os.environ.get("VERITAS_DOCS_ROOT") or DEFAULT_ROOT).expanduser().resolve()
    get_llm: Callable[[], LLM] = llm if callable(llm) else (lambda: llm) if llm is not None else make_llm
    runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
    mcp = FastMCP("veritas", instructions="Evidence-cited stock research briefs. screen_documents -> build_brief -> get_evidence / validate_brief.")

    def docs_path(docs_dir: str) -> str:
        _need(docs_dir, "docs_dir")
        try:
            p = (root / docs_dir).resolve()          # an absolute docs_dir replaces root here; the containment check below decides
        except (OSError, ValueError, RuntimeError) as e:
            raise ToolError(f"Invalid docs_dir: {e}") from e
        if not p.is_relative_to(root):
            raise ToolError(f"docs_dir must be inside the allowed root ({root}); set VERITAS_DOCS_ROOT to change it")
        if not p.is_dir():
            raise ToolError(f"docs_dir does not exist or is not a directory: {p}")
        return str(p)

    def get_run(run_id: str) -> dict[str, Any]:
        if run_id not in runs:
            raise ToolError(f"Unknown run_id {run_id!r}; call build_brief first (runs are kept in memory, newest {MAX_RUNS})")
        return runs[run_id]

    @mcp.tool()
    def screen_documents(docs_dir: str, ticker: str, company: str) -> dict[str, Any]:
        """Load the documents and run the identity gate and injection scan (no LLM). Returns, per document, whether it is about the company and any redacted instruction-like text."""
        _need(ticker, "ticker")
        with _clean_errors():
            docs, diagnostics = load_documents(docs_path(docs_dir))
            screen(docs, ticker.strip(), _need(company, "company"))
        return {"documents": [{"id": d.doc_id, "filename": d.filename, "date": d.published, "relevant": d.relevant,
                               "reason": d.relevance_reason, "injection_flags": d.injection_flags} for d in docs],
                "excluded": [d.doc_id for d in docs if not d.relevant], "diagnostics": diagnostics}

    @mcp.tool()
    async def build_brief(ticker: str, docs_dir: str, company: str, use_entailment: bool = True) -> dict[str, Any]:
        """Build the evidence-cited research brief with the graph engine (robust mode, 3-6 LLM calls, about two minutes live). Keeps the run under a run_id for get_evidence and validate_brief."""
        _need(ticker, "ticker")
        _need(company, "company")
        directory = docs_path(docs_dir)
        with _clean_errors():
            model = get_llm()
            state = await anyio.to_thread.run_sync(lambda: run_graph_state(ticker.strip(), company.strip(), directory, model, today, None, use_entailment))
            res = to_result(state, model)
        run_id = uuid.uuid4().hex[:12]
        runs[run_id] = state
        while len(runs) > MAX_RUNS:
            runs.popitem(last=False)
        errors = [i.model_dump() for i in res.validation.issues if i.severity == "error"]
        return {"run_id": run_id, "brief": res.brief, "passed": res.validation.ok, "validation": "PASS" if res.validation.ok else "FAIL",
                "errors": errors, "evidence_count": len(res.evidence), "llm_calls": res.llm_calls, "repairs": res.repairs, "seconds": round(res.seconds, 1)}

    @mcp.tool()
    def get_evidence(run_id: str) -> dict[str, Any]:
        """The evidence registry (verbatim quotes with document and line range), Python calculations and cross-source conflicts for a run."""
        s = get_run(run_id)
        return {"evidence": [e.model_dump() for e in s["evidence"]], "calculations": [c.model_dump() for c in s["calcs"]],
                "conflicts": [c.model_dump() for c in s["conflicts"]]}

    @mcp.tool(name="validate_brief")
    def validate_brief_tool(run_id: str, brief_markdown: str) -> dict[str, Any]:
        """Re-run the deterministic validator (no LLM) on an edited brief against the run's evidence registry: citations, numbers, conflict disclosure, leakage."""
        s = get_run(run_id)
        if not brief_markdown.strip():
            raise ToolError("brief_markdown must not be empty")
        if len(brief_markdown) > MAX_BRIEF_CHARS:
            raise ToolError(f"brief_markdown is too long ({len(brief_markdown)} > {MAX_BRIEF_CHARS} characters)")
        docs = s["docs"]
        excluded = {d.doc_id for d in docs if not d.relevant}
        markers = exclusion_markers([d for d in docs if not d.relevant], [d for d in docs if d.relevant]) if excluded else []
        r = check_brief(brief_markdown, s["evidence"], s["calcs"], {d.doc_id: d for d in docs}, s["conflicts"], s["lines_by_doc"], excluded, markers)
        return {"ok": r.ok, "issues": [i.model_dump() for i in r.issues], "metrics": r.metrics.model_dump()}

    return mcp


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
