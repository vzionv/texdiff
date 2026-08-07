from pathlib import Path

import pytest

from texdiff.source import SourceError, read_tex_source


def test_nested_inputs(tmp_path: Path):
    (tmp_path / "chapters").mkdir()
    (tmp_path / "main.tex").write_text(r"Before \input{chapters/one} After")
    (tmp_path / "chapters" / "one.tex").write_text(r"One \input{two}")
    (tmp_path / "chapters" / "two.tex").write_text("Two")
    assert "Two" in read_tex_source(tmp_path / "main.tex")


def test_outside_rejected(tmp_path: Path):
    (tmp_path.parent / "outside.tex").write_text("secret")
    (tmp_path / "main.tex").write_text(r"\input{../outside}")
    with pytest.raises(SourceError, match="escapes"):
        read_tex_source(tmp_path / "main.tex")


def test_circular(tmp_path: Path):
    (tmp_path / "a.tex").write_text(r"\input{b}")
    (tmp_path / "b.tex").write_text(r"\input{a}")
    with pytest.raises(SourceError, match="Circular"):
        read_tex_source(tmp_path / "a.tex")


def test_commented_input(tmp_path: Path):
    (tmp_path / "hidden.tex").write_text("NO")
    (tmp_path / "main.tex").write_text("% \\input{hidden}\nVisible")
    assert "NO" not in read_tex_source(tmp_path / "main.tex")
