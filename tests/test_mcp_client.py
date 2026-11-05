"""The MCP client against a real `python -m veritas.mcp_server` subprocess over stdio (screening only, so no LLM and no credentials)."""
import os
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")
pytest.importorskip("langgraph")

from veritas.mcp_client import call, main, open_session  # noqa: E402

SRC = str(Path(__file__).resolve().parents[1] / "src")


def server_env(pack):
    return {"PYTHONPATH": SRC, "VERITAS_DOCS_ROOT": pack, "PATH": os.environ.get("PATH", "")}


def test_client_lists_tools_and_screens_over_a_real_stdio_server(pack):
    async def go():
        async with open_session(env=server_env(pack)) as s:
            names = {t.name for t in (await s.list_tools()).tools}
            res = await call(s, "screen_documents", docs_dir=".", ticker="SRVCABLE", company="Sarvottam Cables Ltd")
            with pytest.raises(RuntimeError, match="inside the allowed root"):
                await call(s, "screen_documents", docs_dir="..", ticker="SRVCABLE", company="Sarvottam Cables Ltd")
            return names, res
    names, res = anyio.run(go)
    assert names == {"screen_documents", "build_brief", "get_evidence", "validate_brief"}
    assert res["excluded"] == ["D6"] and len(res["documents"]) == 8


def test_cli_prints_the_screening_table(pack, monkeypatch, capsys):
    for k, v in server_env(pack).items():
        monkeypatch.setenv(k, v)
    assert main(["SRVCABLE", ".", "--pass-env", "PYTHONPATH", "--pass-env", "VERITAS_DOCS_ROOT", "--pass-env", "PATH"]) == 0
    out = capsys.readouterr().out
    assert "tools: screen_documents" in out and "EXCLUDED" in out
