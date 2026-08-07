from pathlib import Path
import re

import pytest

from texdiff.compare import compare_documents
from texdiff.models import BlockKind, DiffReport, DiffRow, DiffStats, TableData, TextBlock, ChangeKind, ExtractionOptions
from texdiff.pdf import PdfError, backend_status, write_pdf


def sample_report(tmp_path: Path) -> DiffReport:
    old_blocks = [
        TextBlock(0, BlockKind.HEADING, "Introduction", level=1),
        TextBlock(1, BlockKind.PARAGRAPH, "旧版本包含简短的中文正文。"),
    ]
    new_blocks = [
        TextBlock(0, BlockKind.HEADING, "Introduction", level=1),
        TextBlock(1, BlockKind.PARAGRAPH, "新版本包含修改后的中文正文。"),
    ]
    rows, stats = compare_documents(old_blocks, new_blocks)
    return DiffReport(tmp_path / "old.tex", tmp_path / "new.tex", rows, stats, "builtin")


def test_reportlab_native(tmp_path: Path):
    pytest.importorskip("reportlab")
    html = tmp_path / "x.html"
    html.write_text("<h1>Report</h1>", encoding="utf-8")
    pdf, engine = write_pdf(html, tmp_path / "x.pdf", report=sample_report(tmp_path), engine="reportlab")
    assert engine == "reportlab"
    assert pdf.read_bytes()[:5] == b"%PDF-"
    assert pdf.stat().st_size > 1000


def test_native_table_pdf(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    old_table = TableData((("Method", "F1"), ("Base", "88.0")), "Scores")
    new_table = TableData((("Method", "F1"), ("Base", "89.5"), ("New", "91.0")), "Scores")
    old = TextBlock(0, BlockKind.TABLE, old_table.flat_text, table=old_table)
    new = TextBlock(0, BlockKind.TABLE, new_table.flat_text, table=new_table)
    rows, stats = compare_documents([old], [new])
    report = DiffReport(tmp_path / "old.tex", tmp_path / "new.tex", rows, stats, "builtin")
    html = tmp_path / "table.html"
    html.write_text("table", encoding="utf-8")
    pdf, _ = write_pdf(html, tmp_path / "table.pdf", report=report, engine="reportlab")
    assert len(pypdf.PdfReader(str(pdf)).pages) == 1
    assert pdf.stat().st_size > 1500


def test_missing(tmp_path: Path):
    with pytest.raises(PdfError, match="not found"):
        write_pdf(tmp_path / "no.html", tmp_path / "x.pdf")


def test_fallback(monkeypatch, tmp_path: Path):
    import texdiff.pdf as module

    html = tmp_path / "x.html"
    html.write_text("x", encoding="utf-8")
    calls = []

    def fail_r(*args, **kwargs):
        calls.append("r")
        raise RuntimeError()

    def fail_p(*args, **kwargs):
        calls.append("p")
        raise RuntimeError()

    def good_c(_html, pdf):
        calls.append("c")
        pdf.write_bytes(b"%PDF-1.7\n" + b"0" * 200)

    monkeypatch.setattr(module, "_reportlab", fail_r)
    monkeypatch.setattr(module, "_playwright", fail_p)
    monkeypatch.setattr(module, "_chromium", good_c)
    _, engine = write_pdf(html, tmp_path / "x.pdf", report=sample_report(tmp_path))
    assert engine == "chromium"
    assert calls == ["r", "p", "c"]


def test_windows_browser_discovery(tmp_path: Path):
    from texdiff.pdf import _known_browser_paths

    program_files = tmp_path / "Program Files"
    chrome = program_files / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_bytes(b"")
    paths = _known_browser_paths(platform_name="win32", environ={"PROGRAMFILES": str(program_files)})
    assert chrome in paths


def test_backend_status_has_native_engine():
    status = backend_status()
    assert status["reportlab"]["available"] is True
    assert "chromium" in status


def test_cjk_uses_cid_font():
    from texdiff.native_pdf import _register_font

    assert _register_font("中文正文") == "STSong-Light"


def test_fixture_pdf_keeps_table_content(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    from texdiff.pipeline import generate_report

    report = generate_report(
        Path("tests/fixtures/old.tex"),
        Path("tests/fixtures/new.tex"),
        html_output=tmp_path / "fixture.html",
        pdf_output=tmp_path / "fixture.pdf",
        extraction=ExtractionOptions(extractor="builtin"),
    )
    text = re.sub(r"\s+", " ", " ".join((page.extract_text() or "") for page in pypdf.PdfReader(str(report.pdf_path)).pages))
    assert "Default settings" in text
    assert "Retries" in text
    assert "old-preview" not in text


def test_reportlab_splits_long_rows_across_pages(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    old_text = "old document text " * 1400
    new_text = "new document text " * 1400
    old = TextBlock(0, BlockKind.PARAGRAPH, old_text)
    new = TextBlock(0, BlockKind.PARAGRAPH, new_text)
    row = DiffRow(old, new, ChangeKind.MODIFIED, old_html=old_text, new_html=new_text)
    report = DiffReport(
        tmp_path / "old.tex",
        tmp_path / "new.tex",
        [row],
        DiffStats(1, 1, 0, 1, 0, 0, 0),
        "builtin",
    )
    html = tmp_path / "long.html"
    html.write_text("long", encoding="utf-8")
    pdf, _ = write_pdf(html, tmp_path / "long.pdf", report=report, engine="reportlab")
    assert len(pypdf.PdfReader(str(pdf)).pages) > 1
