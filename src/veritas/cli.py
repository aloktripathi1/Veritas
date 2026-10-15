from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .llm import LLMError, make_llm
from .loader import LoadError
from .pipeline import MODES, PipelineError, run
from .render import render


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="veritas", description="Veritas: evidence-grounded stock research brief")
    ap.add_argument("ticker")
    ap.add_argument("docs_dir")
    ap.add_argument("--mode", choices=MODES, default="robust")
    ap.add_argument("--company", default="Sarvottam Cables Ltd", help="exact legal name used for identity checks")
    ap.add_argument("--today", default="2026-09-23")
    ap.add_argument("--system-prompt", help="override prompts/system.md (used for ablations only)")
    ap.add_argument("--quiet", action="store_true", help="do not print progress while running")
    ap.add_argument("--no-entailment", action="store_true", help="skip the advisory LLM citation-support check")
    ap.add_argument("--out", default="output.md")
    ap.add_argument("--artifacts", help="directory for evidence.json / run.json")
    a = ap.parse_args(argv)
    try:
        res = run(a.mode, a.ticker, a.company, a.docs_dir, make_llm(), a.today, a.system_prompt, not a.no_entailment,
                  None if a.quiet else (lambda m: print(m, flush=True)))
    except (LoadError, LLMError, PipelineError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    out_path = Path(a.out)
    if not res.validation.ok:   # never leave a failed brief where a good one is expected
        out_path = out_path.with_name(out_path.name + ".FAILED")
    out_path.write_text(render(res, a.ticker, a.today), encoding="utf-8")
    if a.artifacts:
        p = Path(a.artifacts)
        p.mkdir(parents=True, exist_ok=True)
        (p / "evidence.json").write_text(json.dumps({"evidence": [e.model_dump() for e in res.evidence], "calculations": [c.model_dump() for c in res.calcs],
                                                     "conflicts": [c.model_dump() for c in res.conflicts]}, indent=1, ensure_ascii=False))
        (p / "raw_extraction.txt").write_text(res.raw_extraction)
        (p / "run.json").write_text(json.dumps({"mode": res.mode, "diagnostics": res.diagnostics, "llm_calls": res.llm_calls, "seconds": round(res.seconds, 1),
                                                "repaired": res.repaired, "repairs": res.repairs, "usage": res.usage, "entailment": res.entailment, "metrics": res.validation.metrics.model_dump(),
                                                "issues": [i.model_dump() for i in res.validation.issues]}, indent=1))
    for line in res.diagnostics:
        print(line)
    print(f"wrote {out_path}; validation {'PASS' if res.validation.ok else 'FAIL'} ({sum(i.severity=='error' for i in res.validation.issues)} errors)")
    return 0 if res.validation.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
