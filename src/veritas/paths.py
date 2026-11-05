"""Confine caller-supplied document directories to one allowed root (used by the MCP server and the HTTP API)."""
from __future__ import annotations

from pathlib import Path


class PathError(ValueError):
    pass


def resolve_docs_dir(root: Path, docs_dir: str) -> Path:
    """Resolve `docs_dir` (relative to `root`, or absolute) and require it to be an existing directory inside `root`, symlinks included."""
    if not docs_dir or not docs_dir.strip():
        raise PathError("docs_dir must not be empty")
    try:
        p = (root / docs_dir).resolve()          # an absolute docs_dir replaces root here; the containment check below decides
    except (OSError, ValueError, RuntimeError) as e:
        raise PathError(f"Invalid docs_dir: {e}") from e
    if not p.is_relative_to(root):
        raise PathError(f"docs_dir must be inside the allowed root ({root}); set VERITAS_DOCS_ROOT to change it")
    if not p.is_dir():
        raise PathError(f"docs_dir does not exist or is not a directory: {p}")
    return p
