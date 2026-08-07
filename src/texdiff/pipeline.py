"""End-to-end report generation for local files and Git revisions."""
from __future__ import annotations

from pathlib import Path
from time import perf_counter

from .compare import compare_documents
from .extract import extract_source
from .git_source import read_git_source
from .models import DiffReport, ExtractionOptions, PdfEngine
from .pdf import write_pdf
from .render import write_html
from .source import read_tex_source


def _generate_from_sources(
    old_source: str,
    new_source: str,
    *,
    old_path: Path,
    new_path: Path,
    old_label: str,
    new_label: str,
    html_output: str | Path,
    pdf_output: str | Path | None,
    extraction: ExtractionOptions,
    match_threshold: float,
    move_threshold: float,
    context: int,
    include_hidden_in_pdf: bool,
    pdf_engine: PdfEngine,
    theme_path: str | Path | None,
    source_seconds: float,
) -> DiffReport:
    total_start = perf_counter()
    extract_start = perf_counter()
    old_result = extract_source(old_source, extraction, label=old_label)
    new_result = extract_source(new_source, extraction, label=new_label)
    extract_seconds = perf_counter() - extract_start

    compare_start = perf_counter()
    rows, stats = compare_documents(
        old_result.blocks,
        new_result.blocks,
        match_threshold=match_threshold,
        move_threshold=move_threshold,
        context=context,
    )
    compare_seconds = perf_counter() - compare_start
    backend = old_result.backend if old_result.backend == new_result.backend else f"{old_result.backend}/{new_result.backend}"
    report = DiffReport(
        old_path,
        new_path,
        rows,
        stats,
        backend,
        [*old_result.warnings, *new_result.warnings],
        old_label=old_label,
        new_label=new_label,
        timings={"source": source_seconds, "extract": extract_seconds, "compare": compare_seconds},
    )

    html_start = perf_counter()
    html_path = write_html(
        report,
        Path(html_output),
        print_hidden_unchanged=include_hidden_in_pdf,
        theme_path=theme_path,
    )
    report.timings["html"] = perf_counter() - html_start
    if pdf_output is not None:
        pdf_start = perf_counter()
        report.pdf_path, report.pdf_engine = write_pdf(
            html_path,
            Path(pdf_output),
            report=report,
            engine=pdf_engine,
            include_hidden_unchanged=include_hidden_in_pdf,
            theme_path=theme_path,
        )
        report.timings["pdf"] = perf_counter() - pdf_start
    report.timings["total"] = source_seconds + (perf_counter() - total_start)
    return report


def generate_report(
    old_path: str | Path,
    new_path: str | Path,
    *,
    html_output: str | Path,
    pdf_output: str | Path | None = None,
    extraction: ExtractionOptions | None = None,
    match_threshold: float = 0.38,
    move_threshold: float = 0.78,
    context: int = 1,
    include_hidden_in_pdf: bool = False,
    pdf_engine: PdfEngine = "auto",
    theme_path: str | Path | None = None,
) -> DiffReport:
    old = Path(old_path).resolve()
    new = Path(new_path).resolve()
    if old.suffix.lower() != ".tex" or new.suffix.lower() != ".tex":
        raise ValueError("Both inputs must be .tex files")
    options = extraction or ExtractionOptions()
    source_start = perf_counter()
    old_source = read_tex_source(old, expand_inputs=options.expand_inputs)
    new_source = read_tex_source(new, expand_inputs=options.expand_inputs)
    source_seconds = perf_counter() - source_start
    return _generate_from_sources(
        old_source,
        new_source,
        old_path=old,
        new_path=new,
        old_label=old.name,
        new_label=new.name,
        html_output=html_output,
        pdf_output=pdf_output,
        extraction=options,
        match_threshold=match_threshold,
        move_threshold=move_threshold,
        context=context,
        include_hidden_in_pdf=include_hidden_in_pdf,
        pdf_engine=pdf_engine,
        theme_path=theme_path,
        source_seconds=source_seconds,
    )


def generate_git_report(
    repo: str | Path,
    file_path: str,
    old_commit: str,
    new_commit: str,
    *,
    html_output: str | Path,
    pdf_output: str | Path | None = None,
    extraction: ExtractionOptions | None = None,
    match_threshold: float = 0.38,
    move_threshold: float = 0.78,
    context: int = 1,
    include_hidden_in_pdf: bool = False,
    pdf_engine: PdfEngine = "auto",
    theme_path: str | Path | None = None,
) -> DiffReport:
    options = extraction or ExtractionOptions()
    source_start = perf_counter()
    old_revision = read_git_source(repo, old_commit, file_path, expand_inputs=options.expand_inputs)
    new_revision = read_git_source(repo, new_commit, file_path, expand_inputs=options.expand_inputs)
    source_seconds = perf_counter() - source_start
    display_path = Path(file_path)
    return _generate_from_sources(
        old_revision.source,
        new_revision.source,
        old_path=display_path,
        new_path=display_path,
        old_label=old_revision.label,
        new_label=new_revision.label,
        html_output=html_output,
        pdf_output=pdf_output,
        extraction=options,
        match_threshold=match_threshold,
        move_threshold=move_threshold,
        context=context,
        include_hidden_in_pdf=include_hidden_in_pdf,
        pdf_engine=pdf_engine,
        theme_path=theme_path,
        source_seconds=source_seconds,
    )
