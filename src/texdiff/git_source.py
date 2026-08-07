"""Read LaTeX sources, including local ``\\input`` files, from Git commits."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess

from .source import SourceError

_INPUT_RE = re.compile(r"(?<!\\)\\(?:input|include)\s*\{([^{}]+)\}")


class GitSourceError(SourceError):
    pass


@dataclass(frozen=True, slots=True)
class GitRevisionSource:
    source: str
    label: str
    commit: str
    file_path: str


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
        raise GitSourceError(detail)
    return result.stdout


def _validate_repo(repo: Path) -> Path:
    repo = repo.resolve()
    if not repo.exists():
        raise GitSourceError(f"Git repository does not exist: {repo}")
    root = Path(_run_git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    return root


def _resolve_commit(repo: Path, commit: str) -> str:
    return _run_git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()


def _normalise_repo_path(raw: str, *, base: PurePosixPath | None = None) -> PurePosixPath:
    value = PurePosixPath(raw.strip())
    if not value.suffix:
        value = value.with_suffix(".tex")
    if base is not None:
        value = base / value
    parts: list[str] = []
    for part in value.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise GitSourceError(f"Included file escapes repository root: {raw}")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise GitSourceError(f"Invalid repository path: {raw}")
    return PurePosixPath(*parts)


def _strip_comment_tail(line: str) -> tuple[str, str]:
    escaped = False
    for index, char in enumerate(line):
        if char == "%" and not escaped:
            return line[:index], line[index:]
        escaped = not escaped if char == "\\" else False
    return line, ""


def read_git_source(
    repo: str | Path,
    commit: str,
    file_path: str,
    *,
    expand_inputs: bool = True,
    max_depth: int = 20,
) -> GitRevisionSource:
    """Read a UTF-8 LaTeX file from a commit without modifying the work tree."""
    root = _validate_repo(Path(repo))
    full_commit = _resolve_commit(root, commit)
    initial = _normalise_repo_path(file_path)
    active: set[PurePosixPath] = set()

    def read_one(current: PurePosixPath, depth: int) -> str:
        if depth > max_depth:
            raise GitSourceError(f"Maximum include depth ({max_depth}) exceeded at {current}")
        if current in active:
            raise GitSourceError(f"Circular LaTeX include detected at {current}")
        active.add(current)
        try:
            text = _run_git(root, "show", f"{full_commit}:{current.as_posix()}")
            if not expand_inputs:
                return text

            def replace(match: re.Match[str]) -> str:
                included = _normalise_repo_path(match.group(1), base=current.parent)
                return "\n" + read_one(included, depth + 1) + "\n"

            expanded: list[str] = []
            for line in text.splitlines(keepends=True):
                code, comment = _strip_comment_tail(line)
                expanded.append(_INPUT_RE.sub(replace, code) + comment)
            return "".join(expanded)
        except GitSourceError as exc:
            message = str(exc)
            if "does not exist" in message or "exists on disk" in message or "Path '" in message:
                raise GitSourceError(f"LaTeX source not found at {full_commit[:12]}:{current}") from exc
            raise
        finally:
            active.remove(current)

    source = read_one(initial, 0)
    label = f"{initial.as_posix()} [{full_commit[:12]}]"
    return GitRevisionSource(source, label, full_commit, initial.as_posix())
