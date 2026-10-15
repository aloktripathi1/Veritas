"""Prompt-injection hygiene. Documents are untrusted data.

This scanner is TELEMETRY plus best-effort neutralisation, not the defence. The real boundaries are:
no tools for the model, untrusted text inside delimiters, and output validation. A paraphrased attack can
evade these patterns (see tests/test_hardening.py and scripts/adversarial_live.py).
"""
from __future__ import annotations

import re
import unicodedata

from .models import Document

HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
ZERO_WIDTH = re.compile("[​-‏⁠⁡-⁤﻿­]")
PLACEHOLDER = "[REDACTED: suspected embedded instruction]"
PATTERNS = [
    r"(ignore|disregard|forget|override|bypass)\W+(all\W+|any\W+)?(the\W+)?(previous|prior|above|earlier|preceding|your)\W+\w*\W*(instruction|guidance|rule|prompt|direction|context)",
    r"do not (mention|reveal|disclose|tell)|don'?t (mention|reveal|tell)|without mentioning|leave out|omit\W+(the|any|all)\W",
    r"(note|message|instruction|directive)s?\W+(for|to)\W+(the\W+)?(ai|llm|assistant|model|agent|summar|bot)",
    r"as an? (ai|llm|language model|assistant)",
    r"you (must|should|are (required|instructed|told)|have to|need to) (to )?(state|say|write|output|rate|report|respond|recommend|conclude|omit|ignore)",
    r"in your (summary|response|answer|brief|report|output)",
    r"(rate|rating|recommend)\w*\W+(this|it|the stock|the company)?\W*(as\W+)?(a\W+)?(strong\W+)?(buy|sell)",
    r"system prompt|ai (assistants?|summar\w+)|summari[sz]ation tools?",
]
PATTERN_RE = re.compile("|".join(PATTERNS), re.I)


def normalise(s: str) -> str:
    return unicodedata.normalize("NFKC", ZERO_WIDTH.sub("", s))


def is_instruction_like(s: str) -> bool:
    return PATTERN_RE.search(normalise(s)) is not None


def sanitize(doc: Document) -> list[str]:
    """Return doc lines with suspected instructions replaced by a placeholder (line count preserved).
    Records flags on the document."""
    flags: list[str] = []
    text = "\n".join(doc.lines)

    def repl(m: re.Match) -> str:
        frag = m.group(0)
        if is_instruction_like(frag):
            flags.append(f"embedded instruction in HTML comment ({len(frag)} chars)")
            return PLACEHOLDER + "\n" * frag.count("\n")
        return frag

    text = HTML_COMMENT.sub(repl, text)
    out: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if i >= doc.body_start and is_instruction_like(line):
            flags.append(f"instruction-like text on line {i}")
            line = PLACEHOLDER
        out.append(line)
    doc.injection_flags = flags
    return out
