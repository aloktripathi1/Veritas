import pytest

from veritas.identity import assess_identity
from veritas.injection import sanitize
from veritas.loader import LoadError, load_documents


def test_loads_all_eight_with_metadata(pack):
    docs, diags = load_documents(pack)
    assert len(docs) == 8 and [d.doc_id for d in docs] == [f"D{i}" for i in range(1, 9)]
    assert all(d.published and d.url and d.source for d in docs)
    assert docs[0].filename == "businessdaily_2026-08-09.md" and docs[0].published == "2026-08-09"


def test_missing_dir_and_bad_files(tmp_path):
    with pytest.raises(LoadError):
        load_documents(tmp_path / "nope")
    (tmp_path / "empty.md").write_text("  \n")
    (tmp_path / "x.pdf").write_bytes(b"%PDF")
    (tmp_path / "ok.md").write_text("---\nsource: s\npublished: 2026-01-01\n---\nhello")
    docs, diags = load_documents(tmp_path)
    assert len(docs) == 1
    assert any("empty file" in d for d in diags) and any("unsupported format" in d for d in diags)


def test_no_documents_is_an_error(tmp_path):
    with pytest.raises(LoadError):
        load_documents(tmp_path)


def test_identity_excludes_similar_name(pack):
    docs, _ = load_documents(pack)
    for d in docs:
        assess_identity(d, "SRVCABLE", "Sarvottam Cables Ltd")
    irrelevant = [d.filename for d in docs if not d.relevant]
    assert irrelevant == ["nagpurcitytimes_2026-08-20.md"]


def test_injection_neutralised_and_line_numbers_preserved(pack):
    docs, _ = load_documents(pack)
    blog = next(d for d in docs if "multibagger" in d.filename)
    out = sanitize(blog)
    assert len(out) == len(blog.lines)
    assert "ignore all previous" not in "\n".join(out).lower()
    assert blog.injection_flags
    clean = next(d for d in docs if "press_release" in d.filename)
    sanitize(clean)
    assert clean.injection_flags == []
