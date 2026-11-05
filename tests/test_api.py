"""HTTP API through FastAPI's TestClient with a scripted model and a temporary SQLite database. Offline."""
import json
import logging

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("langgraph")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from tests.test_pipeline import EXTRACTION, GOOD  # noqa: E402
from veritas.api import create_app  # noqa: E402
from veritas.db import Store  # noqa: E402
from veritas.llm import ScriptedLLM  # noqa: E402
from veritas.logging_setup import JsonFormatter  # noqa: E402

BODY = {"ticker": "SRVCABLE", "company": "Sarvottam Cables Ltd", "docs_dir": ".", "use_entailment": False}
OBEYED = GOOD.replace("Sarvottam Cables reported", "This is a strong buy [E01]. Sarvottam Cables reported", 1)


@pytest.fixture
def db(tmp_path):
    s = Store(f"sqlite:///{tmp_path / 'api.db'}")
    yield s
    s.close()


def make_client(pack, db, *responses):
    return TestClient(create_app(llm=ScriptedLLM(list(responses)), store=db, docs_root=pack))


def submit(client, **over):
    r = client.post("/briefs", json=BODY | over)
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def test_healthz_checks_the_database(pack, db, monkeypatch):
    c = make_client(pack, db)
    assert c.get("/healthz").json() == {"status": "ok", "database": "ok"}

    def down():
        raise OSError("connection refused")
    monkeypatch.setattr(db.engine, "connect", down)
    r = c.get("/healthz")
    assert r.status_code == 503 and "OSError" in r.json()["detail"] and "refused" not in r.json()["detail"]


@pytest.mark.parametrize("engine", ["graph", "classic"])
def test_submit_poll_evidence_validate_round_trip(pack, db, engine):
    c = make_client(pack, db, EXTRACTION, GOOD)
    run_id = submit(c, engine=engine)
    got = c.get(f"/briefs/{run_id}").json()
    assert got["status"] == "done" and got["passed"] is True and got["errors"] == []
    assert got["brief"].startswith("## Snapshot") and (got["llm_calls"], got["repairs"], got["evidence_count"]) == (2, 0, 13)
    ev = c.get(f"/briefs/{run_id}/evidence").json()
    assert len(ev["evidence"]) == 13 and ev["calculations"] and any(x["metric"] == "revenue" for x in ev["conflicts"])
    ok = c.post(f"/briefs/{run_id}/validate", json={"brief_markdown": GOOD}).json()
    assert ok["ok"] is True and ok["metrics"]["citations_valid"] == ok["metrics"]["citations_total"]
    bad = c.post(f"/briefs/{run_id}/validate", json={"brief_markdown": GOOD.replace("[E05]", "[E99]")}).json()
    assert bad["ok"] is False and any(i["code"] == "unknown_citation" for i in bad["issues"])


def test_a_model_that_fails_validation_is_a_done_run_with_errors(pack, db):
    c = make_client(pack, db, EXTRACTION, OBEYED, OBEYED, OBEYED)
    got = c.get(f"/briefs/{submit(c)}").json()
    assert got["status"] == "done" and got["passed"] is False and got["repairs"] == 2
    assert any(e["code"] == "injection_phrase" for e in got["errors"])


def test_pipeline_failure_is_recorded_not_raised(pack, db):
    c = make_client(pack, db, "I cannot help with that.")
    got = c.get(f"/briefs/{submit(c)}").json()
    assert got["status"] == "failed" and "PipelineError" in got["error"] and got["brief"] is None
    assert c.get(f"/briefs/{got['run_id']}/evidence").status_code == 409


def test_unexpected_exception_hides_internals(pack, db):
    class Bomb:
        name = "bomb"

        def complete(self, system, user):
            raise RuntimeError("secret detail")
    c = TestClient(create_app(llm=Bomb(), store=db, docs_root=pack))
    got = c.get(f"/briefs/{submit(c)}").json()
    assert got["status"] == "failed" and "secret detail" not in got["error"]


def test_paths_outside_the_root_and_missing_dirs_are_422(pack, db):
    c = make_client(pack, db)
    for d in ("../tests", "/etc", "nope"):
        r = c.post("/briefs", json=BODY | {"docs_dir": d})
        assert r.status_code == 422, d
    assert "inside the allowed root" in c.post("/briefs", json=BODY | {"docs_dir": "../tests"}).json()["detail"]
    assert c.get("/briefs").json() == []          # nothing was queued


def test_request_validation_errors_are_422(pack, db):
    c = make_client(pack, db)
    assert c.post("/briefs", json=BODY | {"ticker": ""}).status_code == 422
    assert c.post("/briefs", json={"ticker": "X"}).status_code == 422
    assert c.post("/briefs", json=BODY | {"engine": "magic"}).status_code == 422


def test_unknown_run_and_unfinished_run(pack, db):
    c = make_client(pack, db)
    for path in ("/briefs/nope", "/briefs/nope/evidence"):
        assert c.get(path).status_code == 404
    assert c.post("/briefs/nope/validate", json={"brief_markdown": "x"}).status_code == 404
    queued = db.create_run("SRVCABLE", "Sarvottam Cables Ltd", pack, False)
    assert c.get(f"/briefs/{queued}").json()["status"] == "queued"
    assert c.post(f"/briefs/{queued}/validate", json={"brief_markdown": GOOD}).status_code == 409
    assert c.post(f"/briefs/{queued}/validate", json={"brief_markdown": ""}).status_code == 422


def test_results_survive_an_app_restart(pack, tmp_path):
    url = f"sqlite:///{tmp_path / 'restart.db'}"
    s1, s2 = Store(url), Store(url)
    run_id = submit(make_client(pack, s1, EXTRACTION, GOOD))
    second = TestClient(create_app(llm=ScriptedLLM([]), store=s2, docs_root=pack))
    assert second.get(f"/briefs/{run_id}").json()["passed"] is True
    assert second.post(f"/briefs/{run_id}/validate", json={"brief_markdown": GOOD}).json()["ok"] is True
    s1.close()
    s2.close()


def test_list_and_stats_reflect_past_runs(pack, db):
    c = make_client(pack, db, EXTRACTION, GOOD, EXTRACTION, OBEYED, GOOD)
    submit(c)
    submit(c)
    st = c.get("/stats").json()
    assert st["runs"] == 2 and st["pass_rate"] == 1.0 and st["repairs_histogram"] == {"0": 1, "1": 1}
    assert len(c.get("/briefs", params={"ticker": "SRVCABLE"}).json()) == 2 and c.get("/stats", params={"ticker": "NOPE"}).json()["runs"] == 0


def test_requests_get_an_id_and_a_structured_log_line(pack, db, caplog):
    c = make_client(pack, db)
    with caplog.at_level(logging.INFO, logger="veritas.api"):
        r = c.get("/healthz", headers={"x-request-id": "abc123"})
    assert r.headers["x-request-id"] == "abc123" and c.get("/healthz").headers["x-request-id"]
    rec = next(x for x in caplog.records if x.msg == "request")
    line = json.loads(JsonFormatter().format(rec))
    assert line["request_id"] == "abc123" and line["path"] == "/healthz" and line["status"] == 200 and line["level"] == "INFO"


def test_logs_never_contain_documents_or_prompts(pack, db, caplog):
    c = make_client(pack, db, EXTRACTION, GOOD)
    with caplog.at_level(logging.DEBUG, logger="veritas"):
        submit(c)
    text = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    assert "job finished" in text and "Revenue from operations" not in text and "ignore all previous" not in text.lower()
