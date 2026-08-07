import json
from pathlib import Path
import subprocess

import pytest

from texdiff.cli import main
from texdiff.models import ExtractionOptions
from texdiff.pipeline import generate_git_report, generate_report

F = Path(__file__).parent / "fixtures"


def test_end_to_end(tmp_path: Path):
    report = generate_report(
        F / "old.tex",
        F / "new.tex",
        html_output=tmp_path / "diff.html",
        pdf_output=tmp_path / "diff.pdf",
        extraction=ExtractionOptions(extractor="builtin"),
    )
    assert report.pdf_path.is_file() and report.pdf_engine == "reportlab"
    assert report.stats.modified > 0
    assert report.timings["total"] < 30
    assert "old-results.pdf" not in report.html_path.read_text(encoding="utf-8")


def test_cli_html_only_by_suffix(tmp_path: Path, capsys):
    output = tmp_path / "diff.html"
    code = main([str(F / "old.tex"), str(F / "new.tex"), "--extractor", "builtin", "-o", str(output)])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and output.is_file()
    assert data["pdf"] is None
    assert data["html"] == str(output.resolve())
    assert data["timings_seconds"]["compare"] >= 0


def test_cli_json_summary(tmp_path: Path, capsys):
    summary = tmp_path / "summary.json"
    code = main([
        str(F / "old.tex"), str(F / "new.tex"), "--no-pdf", "--extractor", "builtin",
        "--html", str(tmp_path / "d.html"), "--json-summary", str(summary),
    ])
    assert code == 0
    assert json.loads(summary.read_text(encoding="utf-8"))["stats"]["modified"] > 0
    assert json.loads(capsys.readouterr().out)["pdf"] is None


def test_git_pipeline_and_cli(tmp_path: Path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "x@y.z"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "document.tex").write_text(r"\begin{document}Old configuration note.\end{document}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "old"], check=True)
    old = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    (repo / "document.tex").write_text(r"\begin{document}New configuration note.\end{document}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "new"], check=True)
    new = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    report = generate_git_report(
        repo, "document.tex", old, new,
        html_output=tmp_path / "git.html", pdf_output=None,
        extraction=ExtractionOptions(extractor="builtin"),
    )
    assert old[:12] in report.display_old and new[:12] in report.display_new
    assert report.stats.modified == 1

    code = main([
        "--repo", str(repo), "--git-file", "document.tex", "--old-commit", old,
        "--new-commit", new, "--no-pdf", "--html", str(tmp_path / "cli-git.html"),
    ])
    assert code == 0
    assert old[:12] in json.loads(capsys.readouterr().out)["old"]


def test_non_tex(tmp_path: Path):
    old = tmp_path / "a.txt"
    new = tmp_path / "b.tex"
    old.write_text("a")
    new.write_text("b")
    with pytest.raises(ValueError):
        generate_report(old, new, html_output=tmp_path / "x.html")


def test_doctor(capsys):
    assert main(["--doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["reportlab"]["available"] is True
