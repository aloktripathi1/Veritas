"""MCP server tools, called through the SDK's in-process client session. Offline (ScriptedLLM); no subprocess, no network."""
import anyio
import pytest

pytest.importorskip("mcp")
pytest.importorskip("langgraph")

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from tests.test_pipeline import EXTRACTION, GOOD  # noqa: E402
from veritas.llm import LLMError, ScriptedLLM  # noqa: E402
from veritas.mcp_server import MAX_RUNS, create_server  # noqa: E402

NAME = "Sarvottam Cables Ltd"


def call_tools(server, *calls):
    """Run tool calls in order on one session; returns [(is_error, payload_or_text), ...]."""
    out = []

    async def go():
        async with create_connected_server_and_client_session(server) as client:
            for name, args in calls:
                r = await client.call_tool(name, args)
                out.append((r.isError, r.structuredContent if not r.isError else r.content[0].text))
    anyio.run(go)
    return out


@pytest.fixture
def server(pack, tmp_path):
    return create_server(llm=ScriptedLLM([EXTRACTION, GOOD]), docs_root=pack)


def test_lists_the_four_tools(server):
    async def go():
        async with create_connected_server_and_client_session(server) as client:
            return {t.name for t in (await client.list_tools()).tools}
    assert anyio.run(go) == {"screen_documents", "build_brief", "get_evidence", "validate_brief"}


def test_screen_documents_excludes_the_lookalike_and_flags_the_injection(server, pack):
    [(err, res)] = call_tools(server, ("screen_documents", {"docs_dir": pack, "ticker": "SRVCABLE", "company": NAME}))
    assert not err
    docs = {d["filename"]: d for d in res["documents"]}
    assert len(docs) == 8
    excluded = [d for d in res["documents"] if not d["relevant"]]
    assert excluded and all(d["reason"] for d in excluded)
    assert any("nagpur" in d["filename"].lower() for d in excluded)
    assert [d["filename"] for d in res["documents"] if d["injection_flags"]] and all(d["id"].startswith("D") for d in res["documents"])
    assert res["excluded"] == [d["id"] for d in excluded]


def test_round_trip_build_get_evidence_validate(server, pack):
    # run_ids are only known after build_brief, so drive one session by hand
    out = {}

    async def go():
        async with create_connected_server_and_client_session(server) as client:
            b = await client.call_tool("build_brief", {"ticker": "SRVCABLE", "docs_dir": pack, "company": NAME, "use_entailment": False})
            out["built"] = b.structuredContent
            rid = b.structuredContent["run_id"]
            out["ev"] = (await client.call_tool("get_evidence", {"run_id": rid})).structuredContent
            out["same"] = (await client.call_tool("validate_brief", {"run_id": rid, "brief_markdown": GOOD})).structuredContent
            out["bad"] = (await client.call_tool("validate_brief", {"run_id": rid, "brief_markdown": GOOD.replace("[E05]", "[E99]")})).structuredContent
    anyio.run(go)
    built = out["built"]
    assert built["passed"] and built["validation"] == "PASS" and built["errors"] == []
    assert built["brief"].startswith("## Snapshot") and built["llm_calls"] == 2 and built["repairs"] == 0 and built["evidence_count"] == 13
    ev = out["ev"]
    assert len(ev["evidence"]) == 13 and ev["calculations"] and any(c["metric"] == "revenue" for c in ev["conflicts"])
    assert any("REDACTED" in e["quote"] for e in ev["evidence"])
    assert out["same"]["ok"] is True and out["same"]["metrics"]["citations_valid"] == out["same"]["metrics"]["citations_total"]
    assert out["bad"]["ok"] is False and any(i["code"] == "unknown_citation" for i in out["bad"]["issues"])


def test_path_traversal_and_outside_paths_are_rejected(server, pack, tmp_path):
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "a.md").write_text("---\npublished: 2026-01-01\n---\nhello")
    calls = [("screen_documents", {"docs_dir": d, "ticker": "SRVCABLE", "company": NAME})
             for d in (f"{pack}/../tests", "../tests", "../../../etc", "/etc", str(outside), "/")]
    for (err, text), (_, args) in zip(call_tools(server, *calls), calls):
        assert err and "inside the allowed root" in text, args
    [(err, text)] = call_tools(server, ("build_brief", {"ticker": "SRVCABLE", "docs_dir": "../tests", "company": NAME}))
    assert err and "inside the allowed root" in text


def test_symlink_escaping_the_root_is_rejected(pack, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").mkdir()
    (root / "link").symlink_to(tmp_path / "secret")
    srv = create_server(llm=ScriptedLLM([]), docs_root=root)
    [(err, text)] = call_tools(srv, ("screen_documents", {"docs_dir": "link", "ticker": "X", "company": NAME}))
    assert err and "inside the allowed root" in text


def test_missing_and_non_directory_paths_are_clean_errors(server, pack):
    results = call_tools(server, ("screen_documents", {"docs_dir": "nope", "ticker": "SRVCABLE", "company": NAME}),
                         ("screen_documents", {"docs_dir": "nse_announcement_2026-09-02.md", "ticker": "SRVCABLE", "company": NAME}),
                         ("screen_documents", {"docs_dir": "bad\x00path", "ticker": "SRVCABLE", "company": NAME}))
    assert all(err for err, _ in results)
    assert all("does not exist or is not a directory" in text for text in (results[0][1], results[1][1]))


def test_relative_docs_dir_resolves_under_the_root(server):
    [(err, res)] = call_tools(server, ("screen_documents", {"docs_dir": ".", "ticker": "SRVCABLE", "company": NAME}))
    assert not err and len(res["documents"]) == 8


def test_root_comes_from_the_environment(pack, monkeypatch):
    monkeypatch.setenv("VERITAS_DOCS_ROOT", pack)
    srv = create_server(llm=ScriptedLLM([]))
    [(err, _)] = call_tools(srv, ("screen_documents", {"docs_dir": ".", "ticker": "SRVCABLE", "company": NAME}))
    assert not err


def test_unknown_run_id_is_a_clean_error(server):
    r = call_tools(server, ("get_evidence", {"run_id": "nope"}), ("validate_brief", {"run_id": "nope", "brief_markdown": GOOD}))
    assert all(err and "Unknown run_id 'nope'" in text for err, text in r)


def test_empty_inputs_are_clean_errors(server, pack):
    r = call_tools(server, ("screen_documents", {"docs_dir": pack, "ticker": " ", "company": NAME}),
                   ("build_brief", {"ticker": "SRVCABLE", "docs_dir": pack, "company": ""}))
    assert all(err and "must not be empty" in text for err, text in r)


def test_domain_errors_map_to_tool_errors(pack):
    class Boom:
        name = "boom"

        def complete(self, system, user):
            raise LLMError("model unavailable")

    r = call_tools(create_server(llm=Boom(), docs_root=pack), ("build_brief", {"ticker": "SRVCABLE", "docs_dir": pack, "company": NAME}))
    assert r[0][0] and "LLM error: model unavailable" in r[0][1]
    refusal = create_server(llm=ScriptedLLM(["I cannot help with that."]), docs_root=pack)
    r = call_tools(refusal, ("build_brief", {"ticker": "SRVCABLE", "docs_dir": pack, "company": NAME}))
    assert r[0][0] and "Pipeline error: extraction produced only 0 verified evidence items" in r[0][1]
    r = call_tools(create_server(llm=ScriptedLLM([]), docs_root=pack.rsplit("/", 1)[0]), ("screen_documents", {"docs_dir": "tests", "ticker": "X", "company": NAME}))
    assert r[0][0] and "Could not load documents" in r[0][1]       # a directory with no supported documents


def test_failed_validation_is_reported_not_raised(pack):
    obeyed = GOOD.replace("Sarvottam Cables reported", "This is a strong buy [E01]. Sarvottam Cables reported", 1)
    srv = create_server(llm=ScriptedLLM([EXTRACTION, obeyed, obeyed, obeyed]), docs_root=pack)
    [(err, res)] = call_tools(srv, ("build_brief", {"ticker": "SRVCABLE", "docs_dir": pack, "company": NAME, "use_entailment": False}))
    assert not err and res["passed"] is False and res["repairs"] == 2 and any(e["code"] == "injection_phrase" for e in res["errors"])


def test_old_runs_are_evicted(pack):
    srv = create_server(llm=lambda: ScriptedLLM([EXTRACTION, GOOD]), docs_root=pack)
    ids = []

    async def go():
        async with create_connected_server_and_client_session(srv) as client:
            for _ in range(MAX_RUNS + 1):
                r = await client.call_tool("build_brief", {"ticker": "SRVCABLE", "docs_dir": pack, "company": NAME, "use_entailment": False})
                ids.append(r.structuredContent["run_id"])
            first = await client.call_tool("get_evidence", {"run_id": ids[0]})
            last = await client.call_tool("get_evidence", {"run_id": ids[-1]})
            return first.isError, last.isError
    assert anyio.run(go) == (True, False)


def test_tools_write_nothing_to_stdout(server, pack, capsys):
    call_tools(server, ("screen_documents", {"docs_dir": pack, "ticker": "SRVCABLE", "company": NAME}))
    assert capsys.readouterr().out == ""
