"""Minimal MCP client: start `veritas-mcp` as a subprocess over stdio, list its tools and call them.

    python -m veritas.mcp_client SRVCABLE . [--company "Sarvottam Cables Ltd"] [--build]

Screening needs no credentials. --build runs the full pipeline, so the server process needs your LLM settings: the child gets only a
minimal environment plus the variables named with --pass-env (e.g. --pass-env SRV_PROVIDER --pass-env ANTHROPIC_API_KEY).
Requires the optional `mcp` extra.
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Sequence

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@asynccontextmanager
async def open_session(command: str = sys.executable, args: Sequence[str] = ("-m", "veritas.mcp_server"),
                       env: dict[str, str] | None = None) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(command=command, args=list(args), env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def call(session: ClientSession, tool: str, **arguments: Any) -> dict[str, Any]:
    """Call a tool; a tool error raises RuntimeError with the server's message."""
    r = await session.call_tool(tool, arguments)
    if r.isError:
        raise RuntimeError(r.content[0].text if r.content else f"{tool} failed")
    return r.structuredContent or {}


async def amain(ticker: str, docs_dir: str, company: str, build: bool, pass_env: Sequence[str]) -> int:
    env = {k: os.environ[k] for k in pass_env if k in os.environ} or None
    async with open_session(env=env) as session:
        print("tools:", ", ".join(t.name for t in (await session.list_tools()).tools))
        screened = await call(session, "screen_documents", docs_dir=docs_dir, ticker=ticker, company=company)
        for d in screened["documents"]:
            note = "relevant" if d["relevant"] else f"EXCLUDED: {d['reason']}"
            print(f"  {d['id']} {d['filename']:<34} {d['date']}  {note}" + (f"  | redacted: {d['injection_flags']}" if d["injection_flags"] else ""))
        if not build:
            return 0
        built = await call(session, "build_brief", ticker=ticker, docs_dir=docs_dir, company=company)
        print(f"run {built['run_id']}: {built['validation']} ({len(built['errors'])} errors, {built['evidence_count']} evidence, {built['repairs']} repairs)")
        again = await call(session, "validate_brief", run_id=built["run_id"], brief_markdown=built["brief"])
        print("re-validation of the same brief:", "ok" if again["ok"] else "FAILED")
        return 0 if built["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="veritas-mcp-client", description=__doc__.splitlines()[0])
    ap.add_argument("ticker")
    ap.add_argument("docs_dir", help="relative to the server's VERITAS_DOCS_ROOT")
    ap.add_argument("--company", default="Sarvottam Cables Ltd")
    ap.add_argument("--build", action="store_true", help="also build a brief (LLM calls) and re-validate it")
    ap.add_argument("--pass-env", action="append", default=[], metavar="NAME", help="environment variable to pass to the server process")
    a = ap.parse_args(argv)
    try:
        return anyio.run(amain, a.ticker, a.docs_dir, a.company, a.build, a.pass_env)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
