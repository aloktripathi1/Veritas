"""LangChain adapter: the Veritas MCP tools as LangChain tools, and a small tool-calling agent that uses them.

The tools delegate to the FastMCP server object (`mcp_server.create_server`), so there is one implementation of each tool:
path confinement, error mapping and run storage behave exactly as they do over MCP. Requires the optional `langchain` extra.
Any LangChain chat model that supports tool calling works; `langchain-anthropic` is deliberately not a dependency
(its releases need a newer `anthropic` SDK than the one this repo pins).
"""
from __future__ import annotations

import json
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool, ToolException
from mcp.server.fastmcp import FastMCP

AGENT_PROMPT = (
    "You are a research assistant using the Veritas tools. To brief a stock: screen_documents first and report any excluded or "
    "flagged documents, then build_brief, then answer with the brief's pass/fail result and its validation errors. Never state a "
    "number that is not in a tool result. Use get_evidence to look up a source quote; use validate_brief to check an edited brief. "
    "If a tool returns an error, explain it plainly instead of retrying with invented arguments."
)


async def langchain_tools(server: FastMCP) -> list[StructuredTool]:
    """One StructuredTool per MCP tool, with the MCP JSON schema as its argument schema; MCP tool errors become ToolException."""
    def wrap(name: str):
        async def call(**kwargs: Any) -> str:
            try:
                result = await server.call_tool(name, kwargs)
            except Exception as e:
                raise ToolException(str(e)) from e
            structured = result[1] if isinstance(result, tuple) else result
            return json.dumps(structured, ensure_ascii=False, default=str)
        return call

    return [StructuredTool.from_function(coroutine=wrap(t.name), name=t.name, description=t.description or t.name, args_schema=t.inputSchema,
                                         handle_tool_error=True) for t in await server.list_tools()]


async def build_agent(server: FastMCP, model: BaseChatModel, system_prompt: str = AGENT_PROMPT):
    """A LangGraph-backed tool-calling agent (langchain.agents.create_agent) over the Veritas tools."""
    return create_agent(model, await langchain_tools(server), system_prompt=system_prompt)
