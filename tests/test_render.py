from pathlib import Path

from texdiff.compare import compare_documents
from texdiff.models import BlockKind, DiffReport, TableData, TextBlock
from texdiff.render import render_html, write_html


def report(tmp_path: Path) -> DiffReport:
    old_table = TableData((("Metric", "Value"), ("F1", "88.0")), "Scores")
    new_table = TableData((("Metric", "Value"), ("F1", "89.4")), "Scores")
    old = [TextBlock(0, BlockKind.TABLE, old_table.flat_text, table=old_table)]
    new = [TextBlock(0, BlockKind.TABLE, new_table.flat_text, table=new_table)]
    rows, stats = compare_documents(old, new)
    return DiffReport(tmp_path / "old.tex", tmp_path / "new.tex", rows, stats, "builtin")


def test_self_contained_interactive_html(tmp_path: Path):
    output = render_html(report(tmp_path))
    assert "<!doctype html>" in output
    assert "grid-template-columns" in output
    assert "https://" not in output and "http://" not in output
    for control in ("collapse-all", "highlight-selection", "clear-highlights", "clear-notes", "save-copy"):
        assert f'id="{control}"' in output
    assert 'class="outline-select"' in output
    assert '<table class="latex-table">' in output
    assert "Formula source" not in output
    assert 'id="search"' not in output
    assert "生成于" not in output
    assert "提取器" not in output
    assert "note-button" in output
    assert 'clone.querySelectorAll(".annotation-connector")' in output
    assert 'document.querySelectorAll(".annotation-connector, .annotation-panel")' not in output
    assert "旧版本" not in output and "新版本" not in output
    assert "由 texdiff 生成" not in output


def test_write_and_custom_theme(tmp_path: Path):
    custom = tmp_path / "theme.toml"
    bundled = Path("src/texdiff/assets/theme.toml").read_text(encoding="utf-8")
    custom.write_text(bundled.replace('#edf3f8', '#123456'), encoding="utf-8")
    value = report(tmp_path)
    path = write_html(value, tmp_path / "nested" / "report.html", theme_path=custom)
    assert path.is_file() and value.html_path == path
    assert "#123456" in path.read_text(encoding="utf-8")
