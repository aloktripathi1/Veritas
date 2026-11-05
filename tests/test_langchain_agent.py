"""LangChain adapter and agent over the MCP tools. A scripted chat model drives real tool calls; the pipeline LLM is scripted too. Offline."""
import json

import anyio
import pytest

pytest.importorskip("langchain")
pytest.importorskip("mcp")
pytest.importorskip("langgraph")

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

from tests.test_pipeline import EXTRACTION, GOOD  # noqa: E402
from veritas.langchain_tools import build_agent, langchain_tools  # noqa: E402
from veritas.llm import ScriptedLLM  # noqa: E402
from veritas.mcp_server import create_server  # noqa: E402

NAME = "Sarvottam Cables Ltd"


class ScriptedChat(GenericFakeChatModel):
    """Scripted chat model; create_agent calls bind_tools, which the stock fake does not implement."""
    def bind_tools(self, tools, **kwargs):
        return self


def tool_call(name, args, i):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}", "type": "tool_call"}])


def ask(agent, text):
    return anyio.run(agent.ainvoke, {"messages": [("user", text)]})["messages"]


def test_tools_mirror_the_mcp_tools_and_schemas(pack):
    server = create_server(llm=ScriptedLLM([]), docs_root=pack)
    tools = anyio.run(langchain_tools, server)
    assert {t.name for t in tools} == {"screen_documents", "build_brief", "get_evidence", "validate_brief"}
    screen = next(t for t in tools if t.name == "screen_documents")
    assert screen.args_schema["required"] == ["docs_dir", "ticker", "company"] and "identity gate" in screen.description


def test_agent_screens_then_builds_a_brief_through_tool_calls(pack):
    server = create_server(llm=ScriptedLLM([EXTRACTION, GOOD]), docs_root=pack)
    model = ScriptedChat(messages=iter([
        tool_call("screen_documents", {"docs_dir": ".", "ticker": "SRVCABLE", "company": NAME}, 1),
        tool_call("build_brief", {"ticker": "SRVCABLE", "docs_dir": ".", "company": NAME, "use_entailment": False}, 2),
        AIMessage(content="Brief built; validation PASS."),
    ]))
    msgs = ask(anyio.run(build_agent, server, model), "Brief SRVCABLE")
    results = [json.loads(m.content) for m in msgs if isinstance(m, ToolMessage)]
    assert len(results) == 2 and results[0]["excluded"] == ["D6"]
    assert results[1]["passed"] is True and results[1]["errors"] == [] and results[1]["llm_calls"] == 2
    assert msgs[-1].content == "Brief built; validation PASS."


def test_run_state_is_shared_across_tool_calls(pack):
    server = create_server(llm=ScriptedLLM([EXTRACTION, GOOD]), docs_root=pack)
    built = json.loads(anyio.run(_call, server, "build_brief", {"ticker": "SRVCABLE", "docs_dir": ".", "company": NAME, "use_entailment": False}))
    model = ScriptedChat(messages=iter([tool_call("get_evidence", {"run_id": built["run_id"]}, 1), AIMessage(content="done")]))
    msgs = ask(anyio.run(build_agent, server, model), "evidence?")
    ev = json.loads(next(m for m in msgs if isinstance(m, ToolMessage)).content)
    assert len(ev["evidence"]) == 13


async def _call(server, name, args):
    tool = next(t for t in await langchain_tools(server) if t.name == name)
    return await tool.ainvoke(args)


def test_tool_errors_reach_the_model_as_messages_not_exceptions(pack):
    server = create_server(llm=ScriptedLLM([]), docs_root=pack)
    model = ScriptedChat(messages=iter([
        tool_call("get_evidence", {"run_id": "made-up"}, 1),
        tool_call("screen_documents", {"docs_dir": "../tests", "ticker": "X", "company": NAME}, 2),
        AIMessage(content="Those failed: unknown run and a path outside the allowed root."),
    ]))
    msgs = ask(anyio.run(build_agent, server, model), "try bad things")
    tm = [m for m in msgs if isinstance(m, ToolMessage)]
    assert "Unknown run_id 'made-up'" in tm[0].content and "inside the allowed root" in tm[1].content
    assert msgs[-1].content.startswith("Those failed")
