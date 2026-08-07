from pathlib import Path
from shutil import which

import pytest

from texdiff.extract import ExtractionError, extract_document, extract_source, extract_with_builtin
from texdiff.models import BlockKind, ExtractionOptions

F = Path(__file__).parent / "fixtures"


def test_builtin_ignores_nonprose_and_keeps_formula_source():
    blocks = extract_with_builtin((F / "old.tex").read_text(), ExtractionOptions(extractor="builtin"))
    text = " ".join(block.text for block in blocks)
    assert blocks[0].text == "Overview"
    assert any(block.kind == BlockKind.LIST_ITEM for block in blocks)
    assert "ignored preview" not in text
    assert "old-preview" not in text
    assert r"\(s = p/r\)" in text


def test_math_modes():
    src = r"\begin{document}Value $x+y$.\begin{equation}a=b\end{equation}\end{document}"
    kept = extract_with_builtin(src, ExtractionOptions(math_mode="keep"))
    assert any(block.kind == BlockKind.MATH and block.text == "a=b" for block in kept)
    assert r"\(x+y\)" in kept[0].text
    placeholder = extract_with_builtin(src, ExtractionOptions(math_mode="placeholder"))
    assert "[MATH]" in " ".join(block.text for block in placeholder)
    dropped = extract_with_builtin(src, ExtractionOptions(math_mode="drop"))
    assert "x+y" not in " ".join(block.text for block in dropped)


def test_fixture_keeps_structure_and_filters_graphics():
    source = (F / "old.tex").read_text(encoding="utf-8")
    blocks = extract_with_builtin(source, ExtractionOptions(extractor="builtin"))
    text = " ".join(block.text for block in blocks)
    assert "ignored preview" not in text
    assert "old-preview" not in text
    settings = next(block for block in blocks if block.kind == BlockKind.TABLE and "Default settings" in block.text)
    assert settings.table and settings.table.rows[0][:3] == ("Setting", "Minimum", "Maximum")
    assert any(block.kind == BlockKind.TABLE and "Default settings" in block.text for block in blocks)


def test_minipage_is_preserved_as_source_block():
    source = r"\begin{document}\begin{minipage}{.5\textwidth}A composite figure.\end{minipage}\end{document}"
    blocks = extract_with_builtin(source, ExtractionOptions(extractor="builtin"))
    raw = next(block for block in blocks if block.kind == BlockKind.CODE)
    assert raw.raw_latex and "minipage" in raw.raw_latex
    assert "A composite figure." in raw.text


def test_builtin_table_structure():
    src = r"""
    \begin{document}
    \begin{table}\caption{Results}\begin{tabular}{lrr}
    Method & Acc & F1 \\
    Base & 90.1 & 88.0 \\
    Ours & 92.4 & 90.2 \\
    \end{tabular}\end{table}
    \end{document}
    """
    blocks = extract_with_builtin(src, ExtractionOptions())
    table = next(block for block in blocks if block.kind == BlockKind.TABLE)
    assert table.table is not None
    assert table.table.caption == "Results"
    assert table.table.rows[1] == ("Base", "90.1", "88.0")
    assert table.table.column_count == 3


@pytest.mark.skipif(which("pandoc") is None, reason="no pandoc")
def test_pandoc_semantics_and_table(tmp_path: Path):
    source = r"""
    \documentclass{article}\begin{document}
    \section{Method}Text $x+y$.
    \begin{table}\caption{Scores}\begin{tabular}{lr}A & 1 \\ B & 2 \\ \end{tabular}\end{table}
    \end{document}
    """
    path = tmp_path / "x.tex"
    path.write_text(source, encoding="utf-8")
    result = extract_document(path, ExtractionOptions(extractor="pandoc"))
    assert result.backend == "pandoc"
    assert any(block.kind == BlockKind.HEADING and block.text == "Method" for block in result.blocks)
    assert any(block.kind == BlockKind.TABLE and block.table and block.table.rows for block in result.blocks)
    assert r"\(x+y\)" in " ".join(block.text for block in result.blocks)


def test_auto_uses_fast_builtin(monkeypatch):
    monkeypatch.setattr("texdiff.extract.shutil.which", lambda _: pytest.fail("Pandoc should not be probed"))
    result = extract_source(r"\begin{document}Readable prose.\end{document}", ExtractionOptions())
    assert result.backend == "builtin"
    assert result.blocks[0].text == "Readable prose."


def test_requested_pandoc_failure(monkeypatch):
    monkeypatch.setattr("texdiff.extract.shutil.which", lambda _: "/fake/pandoc")

    class Result:
        returncode = 2
        stderr = "bad input"
        stdout = ""

    monkeypatch.setattr("texdiff.extract.subprocess.run", lambda *a, **k: Result())
    with pytest.raises(ExtractionError, match="bad input"):
        extract_source("text", ExtractionOptions(extractor="pandoc"))
