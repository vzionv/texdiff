#!/usr/bin/env python3
"""Reject private, obsolete, or generated content from the public tree."""
from __future__ import annotations

import argparse
from pathlib import Path
from pathlib import PurePosixPath
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TERMS = (
    "texdiff" + "-pdf",
    "texdiff" + "_pdf",
    "fo" + "pot",
    "FO" + "POT",
    "Doc" + "ker",
    "doc" + "ker",
    "NVD" + "LA",
    "Eye" + "riss",
    "accel" + "erator",
    "System-Level " + "Performance " + "Comparison",
)
FORBIDDEN_FILES = {"Doc" + "ker" + "file", "CHANGE" + "LOG.md", "DELIVERY" + "_REPORT.md", "WINDOWS" + "_FIX.md"}
GENERATED_SUFFIXES = {".html", ".json", ".pdf", ".png", ".whl"}
IGNORED_DIRS = {".git", ".venv", ".pytest_cache", "__pycache__", ".demo-output", ".benchmark-check", ".build-output", "dist", "build"}
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".ps1", ".py", ".sh", ".tex", ".toml", ".txt", ".xml", ".yaml", ".yml"}


def public_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in IGNORED_DIRS or part.endswith(".egg-info") for part in path.relative_to(ROOT).parts)
    ]


def _check_tree(errors: list[str]) -> None:
    for path in public_files():
        relative = path.relative_to(ROOT)
        if relative.name in FORBIDDEN_FILES or any(term in relative.name for term in FORBIDDEN_TERMS):
            errors.append(f"forbidden file name: {relative}")
        if path.suffix.lower() in GENERATED_SUFFIXES and not (relative.parts[:2] == ("docs", "images") and path.suffix.lower() == ".png"):
            errors.append(f"generated artifact is present outside docs/images PNG previews: {relative}")
        data = path.read_bytes()
        for term in FORBIDDEN_TERMS:
            if term.encode("utf-8") in data:
                errors.append(f"forbidden term {term!r}: {relative}")


def _check_wheel(errors: list[str], wheel_path: Path) -> None:
    with zipfile.ZipFile(wheel_path) as wheel:
        infos = wheel.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            errors.append("wheel contains duplicate member names")
        for info in infos:
            name = info.filename
            path = PurePosixPath(name)
            mode = info.external_attr >> 16
            if name.startswith("/") or name.startswith("//") or ":" in name.split("/", 1)[0] or ".." in path.parts or "\\" in name:
                errors.append(f"unsafe wheel path: {name}")
            if stat.S_ISLNK(mode):
                errors.append(f"wheel contains a symbolic link: {name}")
            if ".dist-info/" not in name and "/" not in name:
                errors.append(f"unexpected top-level wheel member: {name}")
            if any(term.encode("utf-8") in name.encode("utf-8") for term in FORBIDDEN_TERMS):
                errors.append(f"forbidden wheel path: {name}")
            data = wheel.read(info)
            if any(term.encode("utf-8") in data for term in FORBIDDEN_TERMS):
                errors.append(f"forbidden wheel content: {name}")
        packages = {name.split("/", 1)[0] for name in names if "/" in name and ".dist-info/" not in name}
        if packages != {"texdiff"}:
            errors.append(f"wheel packages are {sorted(packages)!r}, expected ['texdiff']")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    if args.wheel and not args.wheel.is_file():
        parser.error(f"wheel does not exist: {args.wheel}")
    errors: list[str] = []
    _check_tree(errors)
    if args.wheel:
        _check_wheel(errors, args.wheel.resolve())
    if errors:
        print("Public release check failed:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"Public release check passed for {len(public_files())} public files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
