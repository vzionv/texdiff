#!/usr/bin/env python3
"""Generate a deterministic structured document comparison benchmark."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile

from texdiff.compare import compare_documents
from texdiff.extract import extract_source
from texdiff.models import BlockKind, ExtractionOptions
from texdiff.pipeline import generate_report


def _section(index: int, version: str) -> list[str]:
    label = f"section-{index}"
    adjective = "stable" if version == "old" else "validated"
    formula = r"\frac{accepted}{received}" if version == "old" else r"\frac{accepted}{received + skipped}"
    return [
        rf"\section{{Configuration Area {index}}}\label{{sec:{label}}}",
        f"This {adjective} configuration area contains a short paragraph with a predictable structure and a reviewable setting.",
        f"The selected profile for area {index} is checked before the next processing stage begins.",
        r"\begin{itemize}",
        r"\item Read the local settings.",
        r"\item Normalize each value.",
        r"\item Record the validation result.",
        r"\end{itemize}",
        rf"The area score is $s_{{{index}}} = {'p/r' if version == 'old' else 'q/t'}$.",
        r"\begin{equation}",
        rf"R_{{{index}}} = {formula}",
        r"\end{equation}",
        "",
    ]


def _settings(version: str) -> list[str]:
    row_end = r"\\"
    return [
        r"\begin{table}",
        r"\caption{Runtime settings}",
        r"\begin{tabular}{lrr}",
        f"Setting & Minimum & Maximum {row_end}",
        f"Retries & 1 & {3 if version == 'old' else 5} {row_end}",
        f"Workers & 2 & {8 if version == 'old' else 12} {row_end}",
        *([f"Cache & 0 & 1 {row_end}"] if version == "new" else []),
        r"\end{tabular}",
        r"\end{table}",
        "",
    ]


def document(version: str, sections: int) -> str:
    lines = [r"\documentclass{article}", r"\begin{document}"]
    lines.extend(
        [
            r"\section{Structured Configuration Guide}",
            "This synthetic document contains headings, prose, lists, formulas, and tables for repeatable comparison checks.",
            "",
        ]
    )
    for index in range(1, sections + 1):
        lines.extend(_section(index, version))
    shared = "A uniquely tagged routing note remains unchanged but moves after the settings table in the revised document."
    deleted = "The baseline-only cleanup queue stores retired local paths, stale retry labels, and temporary routing hints before the final settings table. This distinct block exists to exercise deletion handling."
    added = "The revised-only validation ledger records accepted profiles, normalized values, and explicit audit notes after the final settings table. This distinct block exists to exercise insertion handling."
    if version == "old":
        lines.extend([shared, "", deleted, ""])
    lines.extend(_settings(version))
    if version == "new":
        lines.extend([shared, "", added, ""])
    lines.append(r"\end{document}")
    return "\n".join(lines)


def _extract_blocks(path: Path):
    return extract_source(path.read_text(encoding="utf-8"), ExtractionOptions(extractor="builtin")).blocks


def _structure(blocks) -> dict[str, object]:
    counts = Counter(block.kind.value for block in blocks)
    return {
        "counts": {kind.value: counts.get(kind.value, 0) for kind in BlockKind},
        "tables": [
            {
                "rows": len(block.table.rows),
                "columns": block.table.column_count,
                "simple": block.table.simple,
            }
            for block in blocks
            if block.table is not None
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a structured document comparison benchmark.")
    parser.add_argument("--sections", type=int, default=8)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()
    if args.sections < 1:
        parser.error("--sections must be positive")

    context = tempfile.TemporaryDirectory(prefix="texdiff-benchmark-") if args.output_dir is None else None
    output_dir = Path(context.name) if context else args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    old = output_dir / "benchmark-old.tex"
    new = output_dir / "benchmark-new.tex"
    old.write_text(document("old", args.sections), encoding="utf-8")
    new.write_text(document("new", args.sections), encoding="utf-8")
    options = ExtractionOptions(extractor="builtin")
    report = generate_report(
        old,
        new,
        html_output=output_dir / "benchmark.html",
        pdf_output=None if args.no_pdf else output_dir / "benchmark.pdf",
        extraction=options,
        match_threshold=0.65,
    )
    old_blocks = _extract_blocks(old)
    new_blocks = _extract_blocks(new)
    compare_rows, _ = compare_documents(old_blocks, new_blocks, match_threshold=0.65)
    old_structure = _structure(old_blocks)
    new_structure = _structure(new_blocks)
    payload = {
        "sections": args.sections,
        "blocks": {"old": old_structure["counts"], "new": new_structure["counts"]},
        "tables": {"old": old_structure["tables"], "new": new_structure["tables"]},
        "changes": {key: getattr(report.stats, key) for key in ("unchanged", "modified", "added", "deleted", "moved")},
        "rows": len(compare_rows),
        "timings_seconds": {key: round(value, 4) for key, value in report.timings.items()},
        "html_bytes": report.html_path.stat().st_size,
        "pdf_bytes": report.pdf_path.stat().st_size if report.pdf_path else None,
        "output_dir": str(args.output_dir if args.output_dir is not None else output_dir),
    }
    print(json.dumps(payload, indent=2))
    if context:
        context.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
