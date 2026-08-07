from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import __version__
from .models import ExtractionOptions
from .pdf import backend_status
from .pipeline import generate_git_report, generate_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="texdiff",
        description="Create fast side-by-side HTML/PDF diffs from LaTeX files or Git revisions.",
    )
    parser.add_argument("old", type=Path, nargs="?", help="old local .tex file")
    parser.add_argument("new", type=Path, nargs="?", help="new local .tex file")
    parser.add_argument("-o", "--output", type=Path, default=Path("texdiff-report.pdf"))
    parser.add_argument("--html", type=Path, help="single-file HTML output path")
    parser.add_argument("--no-pdf", action="store_true", help="generate HTML only")

    git = parser.add_argument_group("Git revision input")
    git.add_argument("--repo", type=Path, help="Git repository; defaults to current directory")
    git.add_argument("--git-file", help="repository-relative .tex path to compare")
    git.add_argument("--old-commit", help="old commit, branch, or tag")
    git.add_argument("--new-commit", help="new commit, branch, or tag")

    parser.add_argument("--extractor", choices=["auto", "pandoc", "builtin"], default="auto")
    parser.add_argument("--pdf-engine", choices=["auto", "reportlab", "weasyprint", "playwright", "chromium"], default="auto")
    parser.add_argument("--math", dest="math_mode", choices=["keep", "placeholder", "drop"], default="keep")
    parser.add_argument("--math-placeholder", default="[MATH]")
    parser.add_argument("--keep-citations", action="store_true")
    parser.add_argument("--keep-references", action="store_true")
    parser.add_argument("--no-expand-inputs", action="store_true")
    parser.add_argument("--include-code", action="store_true")
    parser.add_argument("--context", type=int, default=1)
    parser.add_argument("--include-unchanged-in-pdf", action="store_true")
    parser.add_argument("--match-threshold", type=float, default=0.38)
    parser.add_argument("--move-threshold", type=float, default=0.78)
    parser.add_argument("--theme", type=Path, help="override bundled developer theme TOML")
    parser.add_argument("--json-summary", type=Path)
    parser.add_argument("--doctor", action="store_true", help="show PDF backend diagnostics and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _git_requested(args: argparse.Namespace) -> bool:
    return any((args.git_file, args.old_commit, args.new_commit))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.doctor:
        print(json.dumps(backend_status(), indent=2, ensure_ascii=False))
        return 0

    use_git = _git_requested(args)
    if use_git:
        missing = [name for name, value in (("--git-file", args.git_file), ("--old-commit", args.old_commit), ("--new-commit", args.new_commit)) if not value]
        if missing:
            parser.error("Git comparison requires " + ", ".join(missing))
        if args.old is not None or args.new is not None:
            parser.error("Do not combine local positional files with Git revision options")
    elif args.old is None or args.new is None:
        parser.error("old and new .tex files are required unless Git revision options are used")

    html_by_suffix = args.output.suffix.lower() in {".html", ".htm"} and args.html is None
    html_output = args.output if html_by_suffix else (args.html or args.output.with_suffix(".html"))
    options = ExtractionOptions(
        extractor=args.extractor,
        math_mode=args.math_mode,
        math_placeholder=args.math_placeholder,
        ignore_citations=not args.keep_citations,
        ignore_references=not args.keep_references,
        expand_inputs=not args.no_expand_inputs,
        include_code=args.include_code,
    )
    common = dict(
        html_output=html_output,
        pdf_output=None if (args.no_pdf or html_by_suffix) else args.output,
        extraction=options,
        match_threshold=args.match_threshold,
        move_threshold=args.move_threshold,
        context=args.context,
        include_hidden_in_pdf=args.include_unchanged_in_pdf,
        pdf_engine=args.pdf_engine,
        theme_path=args.theme,
    )
    try:
        if use_git:
            report = generate_git_report(
                args.repo or Path.cwd(),
                args.git_file,
                args.old_commit,
                args.new_commit,
                **common,
            )
        else:
            report = generate_report(args.old, args.new, **common)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    summary = {
        "old": report.display_old,
        "new": report.display_new,
        "extractor": report.extractor_used,
        "html": str(report.html_path) if report.html_path else None,
        "pdf": str(report.pdf_path) if report.pdf_path else None,
        "pdf_engine": report.pdf_engine,
        "stats": {
            "old_blocks": report.stats.old_blocks,
            "new_blocks": report.stats.new_blocks,
            "unchanged": report.stats.unchanged,
            "modified": report.stats.modified,
            "added": report.stats.added,
            "deleted": report.stats.deleted,
            "moved": report.stats.moved,
        },
        "timings_seconds": {key: round(value, 4) for key, value in report.timings.items()},
        "warnings": report.warnings,
    }
    if args.json_summary:
        args.json_summary.parent.mkdir(parents=True, exist_ok=True)
        args.json_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0
