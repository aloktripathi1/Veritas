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
# modes: --mode baseline | structured | robust (default)
```
Exit code 0 = validation passed, 1 = validation failed (brief saved as `output.md.FAILED`; see `run.json`), 2 = input/LLM/extraction error (e.g. fewer than 8 verified evidence items). Takes about 100 s (3-6 LLM calls; token usage is recorded in `run.json`). Add `--no-entailment` to skip the advisory judge.

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
