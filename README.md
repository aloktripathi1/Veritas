# Veritas: evidence-cited stock research agent

Takes an NSE ticker and a folder of scraped documents and writes a ~one-page, **evidence-cited** research brief (Snapshot, Bull, Bear, Open questions, Sources) for a retail investor. Built for the Super Investing "AI Innovator" assignment. The company is fictional; nothing is fetched from the internet.

**Deliverables:** brief `output.md` · evidence registry `evidence.json` · system prompt `prompts/system.md` (stage prompts alongside) · tests `tests/` · test log `docs/evaluation.md` · requirements `docs/requirements.md` · design `docs/architecture.md`, `docs/decisions/`.

## How it works (short)
1. Load docs, assign ids `D1..D8`, parse source/date/type.
2. Deterministic identity gate (excludes look-alikes such as *Sarvottam Cable Network* / *Pvt Ltd*) and a pattern-based injection neutraliser (telemetry, not the defence).
3. LLM extracts claims, each with a **verbatim quote**; code locates the quote (line range), rejects invented quotes/numbers, short quotes, and values taken from the wrong table column or period -> evidence ids `E##`.
4. Python computes growth, margin change, pledge as % of total shares, capex remaining, GST demand vs profit, and a "not in the sources" data check; detects cross-source conflicts by metric + period.
5. LLM writes the brief citing only `E##`/`C##`. A validator then checks, per sentence: citation resolves, every number is a whole number in the cited items, no metric mix-up, conflicts disclosed, no unrelated-company leakage, no unattributed recommendation, length.
6. An advisory LLM judge checks whether cited quotes support each sentence. Problems feed at most **two** repair passes; if errors remain the brief is written to `output.md.FAILED`, never `output.md`.
7. Code builds a compact Sources section (one line per document) and an audit line; exact quotes are in `evidence.json`.

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # then edit: SRV_PROVIDER, SRV_MODEL, ANTHROPIC_API_KEY
```
Providers: `anthropic` (needs `ANTHROPIC_API_KEY`) or `claude-cli` (uses a logged-in `claude` CLI with tools disabled; no key). `.env` is git-ignored. Model used for the recorded runs: **claude-sonnet-5-5** (reliable instruction-following and JSON at moderate cost; the model rejects `temperature`, so output varies slightly between runs).

## Run
```bash
PYTHONPATH=src python -m veritas SRVCABLE research_pack --out output.md --artifacts runs/latest
# modes: --mode baseline | structured | robust (default); engines: --engine classic (default) | graph
```
Exit code 0 = validation passed, 1 = validation failed (brief saved as `output.md.FAILED`; see `run.json`), 2 = input/LLM/extraction error (e.g. fewer than 8 verified evidence items). Takes about 100 s (3-6 LLM calls; token usage is recorded in `run.json`). Add `--no-entailment` to skip the advisory judge.

## HTTP API
FastAPI service with SQL persistence (`src/veritas/api.py`, `db.py`). A brief is a background job: `POST /briefs` returns `202` and a `run_id`; poll `GET /briefs/{run_id}`.

| Endpoint | Purpose |
|---|---|
| `POST /briefs` | queue a run `{ticker, company, docs_dir, use_entailment, engine: graph\|classic}` |
| `GET /briefs/{id}` | status (`queued/running/done/failed`), brief, validation errors and warnings, counts |
| `GET /briefs/{id}/evidence` | evidence registry, calculations, conflicts (`409` until the run is done) |
| `POST /briefs/{id}/validate` | deterministic re-check of an edited brief against that run's registry (no LLM) |
| `GET /briefs`, `GET /stats` | past runs; pass rate, repair histogram, top validation error codes (SQL aggregates) |
| `GET /healthz` | liveness plus a database check |

```bash
pip install -e '.[api]'                      # add ,postgres for a Postgres URL
veritas-api                                  # http://127.0.0.1:8000/docs  (OpenAPI)
curl -s localhost:8000/briefs -H 'content-type: application/json' \
  -d '{"ticker":"SRVCABLE","company":"Sarvottam Cables Ltd","docs_dir":"."}'
```
Config (env): `VERITAS_DATABASE_URL` (default `sqlite:///veritas.db`; e.g. `postgresql+psycopg://user:pw@host/db`), `VERITAS_DOCS_ROOT` (default `research_pack/`; `docs_dir` must stay inside it), `VERITAS_LOG_LEVEL`. Schema is managed by Alembic migrations that run on startup (`src/veritas/migrations/`). Logs are one JSON object per line and contain ids and counts, never documents, prompts or keys. There is no authentication: put it behind a gateway.

### Docker
```bash
export ANTHROPIC_API_KEY=...        # read from your shell; never baked into the image
docker compose up --build           # api on :8000, Postgres 16 with a healthcheck
```
The `Dockerfile` and `docker-compose.yml` were written but **not built or run** on the machine used for development (no Docker available there); the API itself was run with uvicorn against SQLite, and Postgres was not tested.

## LangGraph engine
The robust pipeline also exists as a LangGraph state graph (`src/veritas/graph.py`): one node per stage, conditional edges for the evidence abort and the bounded judge/repair loop, and a checkpointer that records the state after every node. It reuses the same functions, prompts, validators and thresholds as the classic engine and returns the same `Result`; tests assert identical briefs, issues, prompts, LLM-call counts and repairs.
```bash
pip install -e '.[graph]'     # langgraph==1.2.20
PYTHONPATH=src python -m veritas SRVCABLE research_pack --engine graph --out output.md
```
`--engine classic` is the default; the graph engine supports `--mode robust` only. From Python:
```python
from langgraph.checkpoint.memory import InMemorySaver
from veritas.graph import run_graph
saver = InMemorySaver()
res = run_graph("SRVCABLE", "Sarvottam Cables Ltd", "research_pack", make_llm(), thread_id="demo", checkpointer=saver)   # inspect with node_trace()
```
The diagram of nodes and edges is in `docs/architecture.md`; the reasoning and trade-offs are in `docs/decisions/009-langgraph-engine.md`.

## MCP server
`veritas-mcp` serves four tools over stdio: `screen_documents` (identity gate and injection scan, no LLM), `build_brief` (graph engine; returns a `run_id`), `get_evidence` (registry, calculations, conflicts of a run) and `validate_brief` (deterministic re-check of an edited brief against a run, no LLM).
```bash
pip install -e '.[mcp]'       # mcp==1.30.0 (+ langgraph, which build_brief runs)
veritas-mcp                    # or: python -m veritas.mcp_server
claude mcp add veritas --env VERITAS_DOCS_ROOT=$PWD/research_pack --env SRV_PROVIDER=claude-cli -- $PWD/.venv/bin/veritas-mcp
```
Paths: `docs_dir` is relative to `VERITAS_DOCS_ROOT` (default: this repo's `research_pack/`) or absolute inside it; `..`, symlink escapes and missing directories are rejected as tool errors. The LLM comes from `make_llm()` (same `SRV_PROVIDER`/`SRV_MODEL`/`ANTHROPIC_API_KEY` settings, never logged or returned); `build_brief` takes about as long as a CLI run. Runs are held in memory (newest 20). `mcp` is pinned to 1.30.0 because 2.x renames `FastMCP` to `MCPServer`.

## Test
```bash
python -m pytest -q              # offline suite, no network/key (scripted model); see docs/evaluation.md for counts
python -m pytest -m live -q      # live-model integration, needs a provider
PYTHONPATH=src python scripts/adversarial_live.py [naive]   # live paraphrased-injection runs -> runs/round2/adv_*
python scripts/evaluate.py runs/round2/r5_a runs/round2/r5_b runs/round2/r5_c    # gold-set table
```

## Demo
```bash
FAST=1 bash scripts/demo.sh   # about 2 minutes: live run with streamed progress, brief, conflicts, injection handling, tests
bash scripts/demo.sh           # same, with the advisory support check (2.5-4 minutes depending on repairs)
```
Writes to `runs/demo/` (git-ignored) and never touches `output.md`. `--quiet` turns off the progress output of the CLI itself.

## Troubleshooting
- `ANTHROPIC_API_KEY is not set`: fill `.env` or use `SRV_PROVIDER=claude-cli`.
- `validation FAIL`: read `issues` in `run.json`; the brief is still written so you can inspect it.
- `extraction: output truncated`: raise `max_tokens` in `llm.py`; complete items are kept.

## Limits
Output is not bit-reproducible (LLM). Injection scanning is pattern-based, and live resistance comes mostly from the model. Citation *support* is checked only by an advisory LLM judge, not proven. One corpus, no held-out set. Not investment advice. See `docs/evaluation.md`.
