"""Advisory semantic-support check: one LLM call over (sentence, cited items) pairs.

Limits: it uses the same family of model as the generator, so it can share its blind spots, and it is
non-deterministic. Deterministic validation (validate.py) remains the gate; 'unsupported' verdicts are fed to
the one bounded repair pass and reported, never treated as proof.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from pydantic import BaseModel, ValidationError

from .evidence import Calc
from .models import Evidence
from .validate import CITE, claim_sentences


class Verdict(BaseModel):
    id: int
    verdict: str
    reason: str = ""


def build_payload(brief: str, ev: list[Evidence], calcs: list[Calc]) -> tuple[str, dict[int, str]]:
    evd = {e.evidence_id: e for e in ev}
    cd = {c.calc_id: c for c in calcs}
    items, index = [], {}
    for sec, sent in claim_sentences(brief):
        ids = CITE.findall(sent)
        if not ids:
            continue
        i = len(items) + 1
        index[i] = sent
        items.append({"id": i, "sentence": CITE.sub("", sent).strip(),
                      "cited": [{"id": c, "text": evd[c].quote if c in evd else cd[c].text} for c in ids if c in evd or c in cd]})
    return json.dumps(items, ensure_ascii=False, indent=1), index


def judge(call: Callable[[str, str], str], system: str, template: str, brief: str,
          ev: list[Evidence], calcs: list[Calc]) -> tuple[list[Verdict], dict[int, str], list[str]]:
    payload, index = build_payload(brief, ev, calcs)
    if not index:
        return [], index, []
    raw = call(system, template.format(payload=payload))
    m = re.search(r"\[.*\]", raw, re.S)
    diags: list[str] = []
    out: list[Verdict] = []
    try:
        for it in json.loads(m.group(0)) if m else []:
            try:
                out.append(Verdict.model_validate(it))
            except ValidationError:
                diags.append("entailment: malformed verdict dropped")
    except json.JSONDecodeError:
        diags.append("entailment: judge output was not valid JSON; check skipped")
    return out, index, diags
