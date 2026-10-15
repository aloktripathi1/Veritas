"""Deterministic company-identity gate: is this document about the requested listed company?"""
from __future__ import annotations

import re

from .models import Document

# words that, directly after the company name stem, indicate a DIFFERENT legal entity or business
OTHER_ENTITY = r"(network|networks|pvt|private|llp|enterprises?|traders|trading|services|telecom|broadband|cable\s+tv|bros|brothers|co\b)"


def _alnum_words(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def assess_identity(doc: Document, ticker: str, legal_name: str) -> None:
    """Sets doc.relevant / doc.relevance_reason in place.

    Rules (transparent, conservative):
      1. ticker mentioned                       -> relevant
      2. exact legal name mentioned             -> relevant
      3. name stem (e.g. 'sarvottam cables') in URL/source/body, and NOT followed by a different
         legal form or business word (Network, Pvt, ...), and not the singular 'cable'  -> relevant (weaker)
      4. otherwise                              -> excluded, noting the near-miss names
    """
    body = "\n".join(doc.lines[doc.body_start - 1:])
    text = f"{body}\n{doc.url} {doc.source}"
    if re.search(rf"\b{re.escape(ticker)}\b", text, re.I):
        doc.relevant, doc.relevance_reason = True, f"mentions ticker {ticker}"
        return
    if re.search(re.escape(legal_name), text, re.I):
        doc.relevant, doc.relevance_reason = True, f"mentions legal name '{legal_name}'"
        return
    stem_words = _alnum_words(re.sub(r"\b(ltd|limited)\b", "", legal_name, flags=re.I))   # 'sarvottam cables'
    norm = _alnum_words(text)
    compact_stem = stem_words.replace(" ", "")
    hits = [m for m in re.finditer(rf"{re.escape(stem_words)}\b\s*(\w+)?", norm)]
    conflicting = [m.group(0) for m in hits if m.group(1) and re.fullmatch(OTHER_ENTITY, m.group(1))]
    # URL hosts are concatenated ('sarvottamcables'); check the compact form separately
    url_norm = _alnum_words(doc.url)
    url_conflict = re.search(rf"{re.escape(stem_words)}\s+{OTHER_ENTITY}", url_norm) is not None
    singular = re.search(rf"{re.escape(stem_words[:-1])}\b(?!s)", norm) is not None and not hits
    in_compact = compact_stem in re.sub(r"[^a-z]", "", text.lower())
    if conflicting or url_conflict:
        doc.relevant = False
        doc.relevance_reason = f"name matches a different entity: {sorted(set(conflicting))[:3] or ['(URL)']}"
    elif (hits or in_compact) and not singular:
        doc.relevant = True
        doc.relevance_reason = f"company name '{stem_words}' appears (no ticker/legal suffix); weaker identity match"
    else:
        near = sorted(set(re.findall(rf"{re.escape(legal_name.split()[0])}\s+\w+(?:\s+\w+)?", text)))
        doc.relevant = False
        doc.relevance_reason = f"no ticker or legal name; similar names only: {near[:3]}"


def exclusion_markers(excluded: list[Document], relevant: list[Document]) -> list[str]:
    """Distinctive strings from excluded documents (capitalised names, money amounts) that never occur in
    relevant documents. Their appearance in the brief signals leakage. Derived, not hard-coded."""
    rel_text = "\n".join("\n".join(d.lines) for d in relevant).lower()
    markers: list[str] = []
    for d in excluded:
        body = "\n".join(d.lines[d.body_start - 1:])
        cands = re.findall(r"(?:[A-Z][a-z]+\s+){1,3}[A-Z][a-z]+", body) + re.findall(r"₹\s?[\d,.]+\s?(?:lakh|crore)", body)
        for c in cands:
            c = c.strip()
            if c.lower() not in rel_text and c not in markers and len(c) > 5:
                markers.append(c)
    return markers
