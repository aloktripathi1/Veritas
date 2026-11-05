"""HTTP API (FastAPI) over the pipeline: submit a brief job, poll it, read its evidence, re-validate an edited brief.

Jobs run in a background thread and are persisted to SQL (see db.py), so results survive restarts.
Configuration (env): VERITAS_DATABASE_URL (default sqlite:///veritas.db), VERITAS_DOCS_ROOT (default research_pack/).
There is no authentication: run it behind a gateway, not on the open internet. Requires the optional `api` extra.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from .db import DEFAULT_URL, Store
from .llm import LLM, LLMError, make_llm
from .loader import LoadError
from .logging_setup import configure_logging
from .evidence import Calc
from .models import Conflict, Evidence, Issue
from .paths import PathError, resolve_docs_dir
from .pipeline import DEFAULT_TODAY, PipelineError, run, validate_against_registry
from .validate import Metrics

log = logging.getLogger("veritas.api")
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "research_pack"
MAX_BRIEF_CHARS = 50_000


class BriefRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32, examples=["SRVCABLE"])
    company: str = Field(min_length=1, max_length=200, examples=["Sarvottam Cables Ltd"])
    docs_dir: str = Field(min_length=1, max_length=1000, description="relative to VERITAS_DOCS_ROOT, or absolute inside it")
    use_entailment: bool = True
    engine: Literal["graph", "classic"] = "graph"


class BriefAccepted(BaseModel):
    run_id: str
    status: Literal["queued"] = "queued"


class BriefOut(BaseModel):
    run_id: str
    status: Literal["queued", "running", "done", "failed"]
    ticker: str
    passed: bool | None = None
    brief: str | None = None
    errors: list[Issue] = []
    warnings: list[Issue] = []
    evidence_count: int = 0
    llm_calls: int | None = None
    repairs: int | None = None
    seconds: float | None = None
    error: str | None = None


class EvidenceOut(BaseModel):
    evidence: list[Evidence]
    calculations: list[Calc]
    conflicts: list[Conflict]


class ValidateIn(BaseModel):
    brief_markdown: str = Field(min_length=1, max_length=MAX_BRIEF_CHARS)


class ValidateOut(BaseModel):
    ok: bool
    issues: list[Issue]
    metrics: Metrics


def create_app(llm: LLM | Callable[[], LLM] | None = None, store: Store | None = None, docs_root: str | Path | None = None,
               today: str = DEFAULT_TODAY) -> FastAPI:
    """Build the app. `llm` is an LLM or a factory (default make_llm, called per job so credentials are only needed when a job runs)."""
    root = Path(docs_root or os.environ.get("VERITAS_DOCS_ROOT") or DEFAULT_ROOT).expanduser().resolve()
    db = store or Store(os.environ.get("VERITAS_DATABASE_URL", DEFAULT_URL))
    get_llm: Callable[[], LLM] = llm if callable(llm) else (lambda: llm) if llm is not None else make_llm
    app = FastAPI(title="Veritas API", version="0.1.0", description="Evidence-cited research briefs with deterministic validation.")

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        rid, t0 = request.headers.get("x-request-id") or uuid.uuid4().hex[:12], time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        log.info("request", extra={"request_id": rid, "method": request.method, "path": request.url.path, "status": response.status_code,
                                   "ms": round((time.perf_counter() - t0) * 1000, 1)})
        return response

    def job(run_id: str, req: BriefRequest, directory: str) -> None:
        db.set_status(run_id, "running")
        log.info("job started", extra={"run_id": run_id, "ticker": req.ticker, "engine": req.engine})
        try:
            model = get_llm()
            if req.engine == "graph":
                from .graph import run_graph
                res = run_graph(req.ticker, req.company, directory, model, today, None, req.use_entailment)
            else:
                res = run("robust", req.ticker, req.company, directory, model, today, None, req.use_entailment)
            db.save_result(run_id, res)
            log.info("job finished", extra={"run_id": run_id, "passed": res.validation.ok, "llm_calls": res.llm_calls, "repairs": res.repairs})
        except (LoadError, LLMError, PipelineError) as e:
            db.save_error(run_id, f"{type(e).__name__}: {e}")
            log.warning("job failed", extra={"run_id": run_id, "error": str(e)[:300]})
        except Exception:   # a background job must record any failure rather than die silently
            db.save_error(run_id, "internal error (see server logs)")
            log.exception("job crashed", extra={"run_id": run_id})

    def need_run(run_id: str) -> dict[str, Any]:
        r = db.get_run(run_id)
        if r is None:
            raise HTTPException(404, f"unknown run_id {run_id!r}")
        return r

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        try:
            with db.engine.connect() as c:
                c.execute(text("SELECT 1"))
        except Exception as e:
            raise HTTPException(503, f"database unavailable: {type(e).__name__}") from e
        return {"status": "ok", "database": "ok"}

    @app.post("/briefs", status_code=202, response_model=BriefAccepted, tags=["briefs"])
    def submit_brief(req: BriefRequest, background: BackgroundTasks) -> BriefAccepted:
        try:
            directory = str(resolve_docs_dir(root, req.docs_dir))
        except PathError as e:
            raise HTTPException(422, str(e)) from e
        run_id = db.create_run(req.ticker.strip(), req.company.strip(), directory, req.use_entailment)
        background.add_task(job, run_id, req, directory)
        return BriefAccepted(run_id=run_id)

    @app.get("/briefs", tags=["briefs"])
    def list_briefs(ticker: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return db.list_runs(ticker, max(1, min(limit, 100)))

    @app.get("/briefs/{run_id}", response_model=BriefOut, tags=["briefs"])
    def get_brief(run_id: str) -> BriefOut:
        r = need_run(run_id)
        return BriefOut(run_id=r["id"], status=r["status"], ticker=r["ticker"], passed=r["passed"], brief=r["brief"],
                        errors=[i for i in r["issues"] if i.severity == "error"], warnings=[i for i in r["issues"] if i.severity == "warning"],
                        evidence_count=r["evidence_count"], llm_calls=r["llm_calls"], repairs=r["repairs"], seconds=r["seconds"], error=r["error"])

    @app.get("/briefs/{run_id}/evidence", response_model=EvidenceOut, tags=["briefs"])
    def get_evidence(run_id: str) -> EvidenceOut:
        r = need_run(run_id)
        if r["status"] != "done":
            raise HTTPException(409, f"run is {r['status']}; evidence is available once it is done")
        ev, calcs, conflicts = db.registry(run_id)
        return EvidenceOut(evidence=ev, calculations=calcs, conflicts=conflicts)

    @app.post("/briefs/{run_id}/validate", response_model=ValidateOut, tags=["briefs"])
    def validate(run_id: str, body: ValidateIn) -> ValidateOut:
        r = need_run(run_id)
        if r["status"] != "done":
            raise HTTPException(409, f"run is {r['status']}; validation needs a finished run")
        ev, calcs, conflicts = db.registry(run_id)
        try:
            v = validate_against_registry(body.brief_markdown, r["ticker"], r["company"], r["docs_dir"], ev, calcs, conflicts)
        except LoadError as e:
            raise HTTPException(409, f"source documents are no longer available: {e}") from e
        return ValidateOut(ok=v.ok, issues=v.issues, metrics=v.metrics)

    @app.get("/stats", tags=["briefs"])
    def stats(ticker: str | None = None) -> dict[str, Any]:
        """Pass rate, repair histogram and most frequent validation errors over finished runs."""
        return db.stats(ticker)

    return app


def main() -> None:
    import uvicorn
    configure_logging(os.environ.get("VERITAS_LOG_LEVEL", "INFO"))
    uvicorn.run(create_app(), host=os.environ.get("VERITAS_HOST", "127.0.0.1"), port=int(os.environ.get("VERITAS_PORT", "8000")), log_config=None)


if __name__ == "__main__":
    main()
