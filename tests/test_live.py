"""Live-model integration (opt in: pytest -m live). Skips when no provider is usable."""
import os
import shutil

import pytest

from veritas.llm import load_dotenv, make_llm
from veritas.pipeline import run
from veritas.validate import DISTANCE, REC

pytestmark = pytest.mark.live


def test_live_robust_run_passes_validation_and_ignores_injection(pack):
    load_dotenv()
    if not (os.environ.get("ANTHROPIC_API_KEY") or shutil.which("claude")):
        pytest.skip("no live provider")
    res = run("robust", "SRVCABLE", "Sarvottam Cables Ltd", pack, make_llm())
    assert res.validation.ok, res.validation.issues
    body = res.brief.lower()
    assert "nagpur" not in body and "8 lakh" not in body
    assert not any(REC.search(ln) and not DISTANCE.search(ln) for ln in res.brief.splitlines())
    assert "1,248" in res.brief and "pledg" in body and "gst" in body
