from pathlib import Path
import subprocess

import pytest

from texdiff.git_source import GitSourceError, read_git_source


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, text=True, capture_output=True)
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "workspace" / "sections").mkdir(parents=True)
    (repo / "workspace" / "main.tex").write_text(r"\begin{document}\input{sections/a}\end{document}", encoding="utf-8")
    (repo / "workspace" / "sections" / "a.tex").write_text("Old paragraph.", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "old")
    old = git(repo, "rev-parse", "HEAD")
    (repo / "workspace" / "sections" / "a.tex").write_text("New paragraph.", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "new")
    return repo, old, git(repo, "rev-parse", "HEAD")


def test_read_git_source_expands_includes(tmp_path: Path):
    repo, old, new = make_repo(tmp_path)
    old_source = read_git_source(repo, old, "workspace/main.tex")
    new_source = read_git_source(repo, new, "workspace/main.tex")
    assert "Old paragraph" in old_source.source
    assert "New paragraph" in new_source.source
    assert old[:12] in old_source.label


def test_git_source_rejects_escape(tmp_path: Path):
    repo, old, _ = make_repo(tmp_path)
    with pytest.raises(GitSourceError, match="escapes"):
        read_git_source(repo, old, "../outside.tex")
