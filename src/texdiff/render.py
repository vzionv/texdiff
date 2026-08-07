"""Render a self-contained, interactive HTML report."""
from __future__ import annotations

import html
from importlib import resources
from pathlib import Path
from string import Template

from .models import BlockKind, ChangeKind, DiffReport, DiffRow
from .theme import Theme, load_theme


def _asset(name: str) -> str:
    return resources.files("texdiff").joinpath(f"assets/{name}").read_text(encoding="utf-8")


def _css(theme: Theme) -> str:
    values = {**theme.colors.__dict__} if hasattr(theme.colors, "__dict__") else {
        field: getattr(theme.colors, field) for field in theme.colors.__dataclass_fields__
    }
    values.update(
        font_size_px=theme.html.font_size_px,
        line_height=theme.html.line_height,
        header_position="sticky" if theme.html.sticky_header else "static",
        diff_head_top="0" if theme.html.sticky_header else "0",
    )
    return Template(_asset("report.css")).substitute(values)


def _block_classes(row: DiffRow, side: str) -> str:
    block = row.old if side == "old" else row.new
    if block is None:
        return ""
    classes = [block.kind.value]
    if block.kind == BlockKind.HEADING:
        classes.append(f"level-{block.level}")
    return " ".join(classes)


def _line_number(row: DiffRow, side: str, row_index: int) -> str:
    block = row.old if side == "old" else row.new
    number = str(block.index + 1) if block else ""
    controls = ""
    if side == "old":
        heading = block and block.kind == BlockKind.HEADING
        controls = (
            f'<div class="line-controls">'
            f'<button type="button" class="row-toggle" aria-expanded="true" '
            f'aria-label="Toggle row {row_index + 1}">{"" if heading else "▾"}</button>'
            f'<button type="button" class="note-button" aria-label="Add a note to row {row_index + 1}">✎</button>'
            f'</div>'
        )
    return f'<div class="line-no {side}"><div class="line-number">{number}</div>{controls}</div>'


def _cell(row: DiffRow, side: str) -> str:
    block = row.old if side == "old" else row.new
    value = row.old_html if side == "old" else row.new_html
    if block is None:
        value = '<span class="empty">&lt;empty&gt;</span>'
    note = ""
    if side == "new" and row.change == ChangeKind.MOVED and row.movement_note:
        note = f'<span class="move-note">{html.escape(row.movement_note)}</span>'
    return (
        f'<div class="cell {side} {_block_classes(row, side)}">'
        f'<div class="cell-content">{note}{value}</div></div>'
    )


def _render_row(row: DiffRow, index: int, print_hidden_unchanged: bool) -> str:
    classes = ["diff-row", row.change.value]
    heading_block = next((block for block in (row.new, row.old) if block and block.kind == BlockKind.HEADING), None)
    if heading_block:
        classes.append("heading-row")
    if not row.visible:
        classes.append("hidden-context")
    if row.change == ChangeKind.UNCHANGED and not print_hidden_unchanged:
        classes.append("print-hidden")
    heading_level = heading_block.level if heading_block else 0
    return (
        f'<article class="{" ".join(classes)}" data-change="{row.change.value}" '
        f'data-initial-visible="{str(row.visible).lower()}" data-row="{index}" data-heading-level="{heading_level}">'
        f'{_line_number(row, "old", index)}{_cell(row, "old")}'
        f'{_line_number(row, "new", index)}{_cell(row, "new")}'
        '</article>'
    )


def _outline_html(report: DiffReport, side: str) -> str:
    """Generate outline dropdown for one side."""
    label = report.display_old if side == "old" else report.display_new
    return (
        f'<div class="diff-head-side {side}">'
        f'<span class="diff-head-label">{html.escape(label)}</span>'
        f'<select class="outline-select" data-side="{side}"><option value="">—</option></select>'
        f'</div>'
    )


def render_html(
    report: DiffReport,
    *,
    print_hidden_unchanged: bool = False,
    theme_path: str | Path | None = None,
) -> str:
    theme = load_theme(theme_path)
    stats = report.stats
    badges = "".join(
        f'<span class="badge">{label}: {value}</span>'
        for label, value in (
            ("Modified", stats.modified), ("Added", stats.added), ("Deleted", stats.deleted),
            ("Moved", stats.moved), ("Unchanged", stats.unchanged),
        )
    )
    warnings = ""
    if report.warnings:
        warnings = '<div class="helper">Warning: ' + " · ".join(html.escape(item) for item in report.warnings) + '</div>'
    rows = "".join(_render_row(row, index, print_hidden_unchanged) for index, row in enumerate(report.rows))
    title = f"LaTeX diff: {report.display_old} vs {report.display_new}"
    default_view = theme.html.default_view if theme.html.default_view in {"changes", "context", "all", "unchanged"} else "context"
    download_name = f"{Path(report.display_old).stem}-vs-{Path(report.display_new).stem}-reviewed.html"
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{_css(theme)}</style></head>
<body data-default-view="{default_view}" data-download-name="{html.escape(download_name, quote=True)}">
<header>
  <div class="header-top">
    <h1>{html.escape(title)}</h1>
    <div class="summary">{badges}</div>
  </div>
  <div class="toolbar" role="toolbar" aria-label="Diff viewing tools">
    <div class="toolbar-group">
      <button type="button" data-view="context">Changes + context</button>
      <button type="button" data-view="changes">Changes only</button>
      <button type="button" data-view="all">All content</button>
      <button type="button" data-view="unchanged">Unchanged only</button>
    </div>
    <div class="toolbar-group">
      <button id="collapse-all" type="button">Collapse all</button>
      <button id="expand-all" type="button">Expand all</button>
    </div>
    <div class="toolbar-group">
      <button id="highlight-selection" type="button">Highlight selection</button>
      <button id="add-note" type="button">Add note</button>
      <button id="clear-highlights" type="button">Clear highlights</button>
      <button id="clear-notes" type="button">Clear notes</button>
      <button id="save-copy" type="button">Save HTML</button>
    </div>
  </div>
  <div class="diff-head">
    {_outline_html(report, "old")}
    {_outline_html(report, "new")}
  </div>
  {warnings}
</header>
<main>
<div class="diff-body">{rows or '<div class="empty">No differences found.</div>'}</div></main>
<script>{_asset("report.js")}</script></body></html>'''


def write_html(
    report: DiffReport,
    output: Path,
    *,
    print_hidden_unchanged: bool = False,
    theme_path: str | Path | None = None,
) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_html(report, print_hidden_unchanged=print_hidden_unchanged, theme_path=theme_path),
        encoding="utf-8",
    )
    report.html_path = output
    return output