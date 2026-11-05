"""SQL persistence: migrations, storing a run, reading it back, and the aggregate queries. SQLite files in tmp_path; no network."""
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("alembic")

from sqlalchemy import create_engine, inspect  # noqa: E402

from tests.test_pipeline import EXTRACTION, GOOD  # noqa: E402
from veritas.db import Base, Store, upgrade  # noqa: E402
from veritas.llm import ScriptedLLM  # noqa: E402
from veritas.pipeline import run  # noqa: E402

OBEYED = GOOD.replace("Sarvottam Cables reported", "This is a strong buy [E01]. Sarvottam Cables reported", 1)


def finished(pack, responses):
    return run("robust", "SRVCABLE", "Sarvottam Cables Ltd", pack, ScriptedLLM(responses), use_entailment=False)


@pytest.fixture
def store(tmp_path):
    s = Store(f"sqlite:///{tmp_path / 'v.db'}")
    yield s
    s.close()


def test_migrations_match_the_orm_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    engine = create_engine(url)
    insp = inspect(engine)
    assert set(insp.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
    for name, table in Base.metadata.tables.items():
        assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}, name
    assert {i["name"] for i in insp.get_indexes("runs")} == {"ix_runs_ticker_created"}
    engine.dispose()
    upgrade(url)   # idempotent


def test_in_memory_store_works(pack):
    s = Store("sqlite://")
    rid = s.create_run("SRVCABLE", "Sarvottam Cables Ltd", pack, False)
    s.save_result(rid, finished(pack, [EXTRACTION, GOOD]))
    assert s.get_run(rid)["status"] == "done"
    s.close()


def test_run_round_trips_through_the_database(store, pack):
    res = finished(pack, [EXTRACTION, GOOD])
    rid = store.create_run("SRVCABLE", "Sarvottam Cables Ltd", pack, False)
    assert store.get_run(rid)["status"] == "queued"
    store.save_result(rid, res)
    row = store.get_run(rid)
    assert (row["status"], row["passed"], row["llm_calls"], row["repairs"], row["brief"]) == ("done", True, 2, 0, res.brief)
    assert row["evidence_count"] == len(res.evidence) == 13 and row["issues"] == res.validation.issues
    ev, calcs, conflicts = store.registry(rid)
    assert ev == sorted(res.evidence, key=lambda e: e.evidence_id) and calcs == res.calcs and conflicts == res.conflicts


def test_unknown_run_is_none_and_errors_are_recorded(store, pack):
    assert store.get_run("nope") is None
    rid = store.create_run("SRVCABLE", "X", pack, True)
    store.save_error(rid, "Pipeline error: boom")
    assert store.get_run(rid)["status"] == "failed" and store.get_run(rid)["error"] == "Pipeline error: boom"


def test_stats_aggregate_past_runs(store, pack):
    plans = [[EXTRACTION, GOOD], [EXTRACTION, OBEYED, GOOD], [EXTRACTION, OBEYED, OBEYED, OBEYED]]   # 0 repairs pass, 1 repair pass, 2 repairs fail
    for responses in plans:
        rid = store.create_run("SRVCABLE", "Sarvottam Cables Ltd", pack, False)
        store.save_result(rid, finished(pack, responses))
    store.save_error(store.create_run("SRVCABLE", "Sarvottam Cables Ltd", pack, False), "down")
    store.save_result(rid2 := store.create_run("OTHER", "Other Ltd", pack, False), finished(pack, [EXTRACTION, GOOD]))
    st = store.stats("SRVCABLE")
    assert st["runs"] == 3 and st["passed"] == 2 and st["pass_rate"] == 0.667 and st["errored_runs"] == 1
    assert st["repairs_histogram"] == {0: 1, 1: 1, 2: 1} and st["mean_llm_calls"] == 3.0
    assert st["top_error_codes"][0]["code"] == "injection_phrase" and st["top_error_codes"][0]["count"] >= 1
    assert store.stats()["runs"] == 4 and store.stats("NOPE")["pass_rate"] is None
    assert [r["ticker"] for r in store.list_runs("OTHER")] == ["OTHER"] and store.list_runs()[0]["id"] == rid2
