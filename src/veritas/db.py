"""SQL persistence for runs (SQLite by default, Postgres by URL) via SQLAlchemy 2.x; schema managed by Alembic.

`Store` is the only thing the API talks to. Tables: runs (one row per brief), evidence, calculations, conflicts, issues.
Requires the optional `api` extra.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from .evidence import Calc
from .models import Conflict, Evidence, Issue

DEFAULT_URL = "sqlite:///veritas.db"
STATUSES = ("queued", "running", "done", "failed")


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ticker: Mapped[str] = mapped_column(String(32))
    company: Mapped[str] = mapped_column(String(200))
    docs_dir: Mapped[str] = mapped_column(String(1000))
    use_entailment: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(16))
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    brief: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    repairs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EvidenceRow(Base):
    __tablename__ = "evidence"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(16))
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    quote: Mapped[str] = mapped_column(Text)
    metric: Mapped[str] = mapped_column(String(200))
    entity: Mapped[str] = mapped_column(String(200))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(64))
    period: Mapped[str] = mapped_column(String(100))
    claim_type: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text)


class CalcRow(Base):
    __tablename__ = "calculations"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    calc_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    inputs: Mapped[list] = mapped_column(JSON)


class ConflictRow(Base):
    __tablename__ = "conflicts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(200))
    period: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))
    evidence_ids: Mapped[list] = mapped_column(JSON)
    values: Mapped[list] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)


class IssueRow(Base):
    __tablename__ = "issues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    severity: Mapped[str] = mapped_column(String(16))
    code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str, engine: Engine | None = None) -> None:
    """Apply all Alembic migrations (through `engine` when given, so in-memory databases work)."""
    cfg = alembic_config(url)
    if engine is not None:
        cfg.attributes["engine"] = engine
    command.upgrade(cfg, "head")


def make_engine(url: str) -> Engine:
    if url in ("sqlite://", "sqlite:///:memory:"):   # one shared connection so an in-memory database survives across sessions
        return create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


class Store:
    def __init__(self, url: str = DEFAULT_URL, migrate: bool = True):
        self.url = url
        self.engine = make_engine(url)
        if migrate:
            upgrade(url, self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    # ---- writes ----
    def create_run(self, ticker: str, company: str, docs_dir: str, use_entailment: bool) -> str:
        run_id = uuid.uuid4().hex[:12]
        with self._sessions.begin() as s:
            s.add(RunRow(id=run_id, created_at=datetime.now(timezone.utc), ticker=ticker, company=company, docs_dir=docs_dir,
                         use_entailment=use_entailment, status="queued"))
        return run_id

    def set_status(self, run_id: str, status: str) -> None:
        assert status in STATUSES
        with self._sessions.begin() as s:
            s.get(RunRow, run_id).status = status

    def save_result(self, run_id: str, res) -> None:
        """Persist a finished pipeline Result: the run row, the registry, the calculations, the conflicts and every validation issue."""
        with self._sessions.begin() as s:
            r = s.get(RunRow, run_id)
            r.status, r.passed, r.brief = "done", res.validation.ok, res.brief
            r.llm_calls, r.repairs, r.seconds = res.llm_calls, res.repairs, round(res.seconds, 2)
            s.add_all(EvidenceRow(run_id=run_id, **e.model_dump()) for e in res.evidence)
            s.add_all(CalcRow(run_id=run_id, **c.model_dump()) for c in res.calcs)
            s.add_all(ConflictRow(run_id=run_id, **c.model_dump()) for c in res.conflicts)
            s.add_all(IssueRow(run_id=run_id, **i.model_dump()) for i in res.validation.issues)

    def save_error(self, run_id: str, message: str) -> None:
        with self._sessions.begin() as s:
            r = s.get(RunRow, run_id)
            r.status, r.error = "failed", message[:2000]

    # ---- reads ----
    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._sessions() as s:
            r = s.get(RunRow, run_id)
            if r is None:
                return None
            issues = s.scalars(select(IssueRow).where(IssueRow.run_id == run_id).order_by(IssueRow.id)).all()
            n_ev = s.scalar(select(func.count()).select_from(EvidenceRow).where(EvidenceRow.run_id == run_id))
            return {"id": r.id, "created_at": r.created_at, "ticker": r.ticker, "company": r.company, "docs_dir": r.docs_dir,
                    "use_entailment": r.use_entailment, "status": r.status, "passed": r.passed, "brief": r.brief, "llm_calls": r.llm_calls,
                    "repairs": r.repairs, "seconds": r.seconds, "error": r.error, "evidence_count": n_ev,
                    "issues": [Issue(severity=i.severity, code=i.code, message=i.message) for i in issues]}

    def registry(self, run_id: str) -> tuple[list[Evidence], list[Calc], list[Conflict]]:
        with self._sessions() as s:
            ev = [Evidence(**{k: getattr(e, k) for k in Evidence.model_fields}) for e in s.scalars(
                select(EvidenceRow).where(EvidenceRow.run_id == run_id).order_by(EvidenceRow.evidence_id))]
            calcs = [Calc(calc_id=c.calc_id, text=c.text, inputs=c.inputs) for c in s.scalars(
                select(CalcRow).where(CalcRow.run_id == run_id).order_by(CalcRow.calc_id))]
            conflicts = [Conflict(metric=c.metric, period=c.period, kind=c.kind, evidence_ids=c.evidence_ids, values=c.values, explanation=c.explanation)
                         for c in s.scalars(select(ConflictRow).where(ConflictRow.run_id == run_id).order_by(ConflictRow.id))]
            return ev, calcs, conflicts

    def list_runs(self, ticker: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._sessions() as s:
            q = select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
            if ticker:
                q = q.where(RunRow.ticker == ticker)
            return [{"id": r.id, "created_at": r.created_at, "ticker": r.ticker, "status": r.status, "passed": r.passed, "repairs": r.repairs}
                    for r in s.scalars(q)]

    def stats(self, ticker: str | None = None) -> dict[str, Any]:
        """Aggregates over finished runs: pass rate, repair histogram, mean calls and seconds, most frequent validation error codes."""
        with self._sessions() as s:
            where = [RunRow.status == "done"] + ([RunRow.ticker == ticker] if ticker else [])
            total, passed, calls, secs = s.execute(select(
                func.count(), func.coalesce(func.sum(func.cast(RunRow.passed, Integer)), 0), func.avg(RunRow.llm_calls), func.avg(RunRow.seconds)
            ).where(*where)).one()
            hist = dict(s.execute(select(RunRow.repairs, func.count()).where(*where).group_by(RunRow.repairs).order_by(RunRow.repairs)).all())
            codes = s.execute(select(IssueRow.code, func.count().label("n")).join(RunRow, RunRow.id == IssueRow.run_id)
                              .where(*where, IssueRow.severity == "error").group_by(IssueRow.code).order_by(func.count().desc(), IssueRow.code).limit(10)).all()
            failed = s.scalar(select(func.count()).select_from(RunRow).where(RunRow.status == "failed", *([RunRow.ticker == ticker] if ticker else [])))
        return {"runs": total, "passed": int(passed), "pass_rate": round(passed / total, 3) if total else None,
                "mean_llm_calls": round(calls, 2) if calls is not None else None, "mean_seconds": round(secs, 2) if secs is not None else None,
                "repairs_histogram": {int(k): v for k, v in hist.items()}, "top_error_codes": [{"code": c, "count": n} for c, n in codes],
                "errored_runs": failed}
