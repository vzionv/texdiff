from pathlib import Path

import pytest

from texdiff.theme import load_theme


def test_bundled_theme_uses_cool_modified_background():
    theme = load_theme()
    assert theme.colors.modified_background.lower() != "#fff8c5"
    assert theme.html.default_view == "context"


def test_invalid_theme(tmp_path: Path):
    path = tmp_path / "bad.toml"
    path.write_text("[colors]\ntext='#000000'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid texdiff theme"):
        load_theme(path)


def test_theme_rejects_css_injection(tmp_path: Path):
    path = tmp_path / "unsafe.toml"
    source = Path("src/texdiff/assets/theme.toml").read_text(encoding="utf-8")
    path.write_text(source.replace('#ffffff', 'red; body { display: none; }'), encoding="utf-8")
    with pytest.raises(ValueError, match="theme color"):
        load_theme(path)
