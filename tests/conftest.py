from pathlib import Path

import pytest

PACK = Path(__file__).resolve().parents[1] / "research_pack"


@pytest.fixture(scope="session")
def pack():
    return str(PACK)
