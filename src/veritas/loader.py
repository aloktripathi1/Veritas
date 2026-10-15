"""Document discovery and parsing. Never silently drops a file."""
from __future__ import annotations

import re
from pathlib import Path

from .models import Document

SUPPORTED = {".md", ".txt"}
FRONT = re.compile(r"^(source|url|published|type):\s*(.*)$")


class LoadError(Exception):
    pass


def load_documents(directory: str | Path) -> tuple[list[Document], list[str]]:
    """Return (documents, diagnostics). Unsupported/empty files are reported, not hidden."""
    d = Path(directory)
    if not d.is_dir():
        raise LoadError(f"Document directory not found: {d}")
    diagnostics: list[str] = []
    docs: list[Document] = []
    files = sorted(p for p in d.iterdir() if p.is_file())
    n = 0
    for p in files:
        if p.suffix.lower() not in SUPPORTED:
            diagnostics.append(f"SKIPPED {p.name}: unsupported format {p.suffix!r}")
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            diagnostics.append(f"SKIPPED {p.name}: not valid UTF-8")
            continue
        if not text.strip():
            diagnostics.append(f"SKIPPED {p.name}: empty file")
            continue
        n += 1
        doc = parse_document(f"D{n}", p.name, text)
        if not doc.published:
            diagnostics.append(f"WARNING {p.name}: no 'published' date in front matter")
        docs.append(doc)
    if not docs:
        raise LoadError(f"No supported documents found in {d}")
    diagnostics.append(f"Loaded {len(docs)} document(s) of {len(files)} file(s) in {d}")
    return docs, diagnostics


def parse_document(doc_id: str, filename: str, text: str) -> Document:
    lines = text.splitlines()
    meta: dict[str, str] = {}
    body_start = 1
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], start=2):
            if line.strip() == "---":
                body_start = i + 1
                break
            m = FRONT.match(line)
            if m:
                meta[m.group(1)] = m.group(2).strip()
    return Document(
        doc_id=doc_id, filename=filename, source=meta.get("source", ""), url=meta.get("url", ""),
        published=meta.get("published", ""), doc_type=meta.get("type", ""), lines=lines, body_start=body_start,
    )


def numbered(doc: Document, text_lines: list[str] | None = None) -> str:
    """Render the body with stable line numbers (used both for prompting and for citations)."""
    src = text_lines if text_lines is not None else doc.lines
    return "\n".join(f"L{i}: {src[i - 1]}" for i in range(doc.body_start, len(src) + 1))
