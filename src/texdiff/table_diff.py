"""Small-table alignment shared by HTML and native PDF renderers."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Callable

from .models import TableData


@dataclass(frozen=True, slots=True)
class AlignedTableRow:
    old: tuple[str, ...] | None
    new: tuple[str, ...] | None


def _key(row: tuple[str, ...]) -> str:
    return "\x1f".join(re.sub(r"\s+", " ", cell).strip().casefold() for cell in row)


def _row_similarity(old: tuple[str, ...], new: tuple[str, ...]) -> float:
    a, b = _key(old), _key(new)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    positional = sum(1 for i in range(min(len(old), len(new))) if old[i].strip().casefold() == new[i].strip().casefold())
    positional /= max(len(old), len(new), 1)
    ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
    return 0.72 * ratio + 0.28 * positional


def align_table_rows(old: TableData | None, new: TableData | None) -> list[AlignedTableRow]:
    old_rows = list(old.rows if old else ())
    new_rows = list(new.rows if new else ())
    n, m = len(old_rows), len(new_rows)
    if not n:
        return [AlignedTableRow(None, row) for row in new_rows]
    if not m:
        return [AlignedTableRow(row, None) for row in old_rows]

    gap = -0.42
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    moves = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i * gap
        moves[i][0] = "up"
    for j in range(1, m + 1):
        dp[0][j] = j * gap
        moves[0][j] = "left"
    similarities = [[_row_similarity(o, nn) for nn in new_rows] for o in old_rows]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sim = similarities[i - 1][j - 1]
            diagonal = dp[i - 1][j - 1] + (1.6 * sim - 0.55)
            up = dp[i - 1][j] + gap
            left = dp[i][j - 1] + gap
            best = max(diagonal, up, left)
            dp[i][j] = best
            moves[i][j] = "diag" if best == diagonal else "up" if best == up else "left"

    result: list[AlignedTableRow] = []
    i, j = n, m
    while i or j:
        direction = moves[i][j]
        if direction == "diag":
            sim = similarities[i - 1][j - 1]
            if sim >= 0.22:
                result.append(AlignedTableRow(old_rows[i - 1], new_rows[j - 1]))
            else:
                result.extend((AlignedTableRow(old_rows[i - 1], None), AlignedTableRow(None, new_rows[j - 1])))
            i -= 1
            j -= 1
        elif direction == "up":
            result.append(AlignedTableRow(old_rows[i - 1], None))
            i -= 1
        else:
            result.append(AlignedTableRow(None, new_rows[j - 1]))
            j -= 1
    result.reverse()
    return result


def table_cell_status(
    old_row: tuple[str, ...] | None,
    new_row: tuple[str, ...] | None,
    column: int,
) -> tuple[str | None, str | None, str]:
    old_cell = old_row[column] if old_row is not None and column < len(old_row) else None
    new_cell = new_row[column] if new_row is not None and column < len(new_row) else None
    if old_cell is None and new_cell is not None:
        return old_cell, new_cell, "added"
    if new_cell is None and old_cell is not None:
        return old_cell, new_cell, "deleted"
    if (old_cell or "").strip() == (new_cell or "").strip():
        return old_cell, new_cell, "unchanged"
    return old_cell, new_cell, "modified"


def render_table_html(
    old: TableData | None,
    new: TableData | None,
    *,
    side: str,
    token_diff: Callable[[str, str], tuple[str, str]],
) -> str:
    import html

    table = old if side == "old" else new
    if table is None:
        return '<div class="empty">&lt;empty&gt;</div>'
    aligned = align_table_rows(old, new)
    columns = max(old.column_count if old else 0, new.column_count if new else 0, 1)
    caption = html.escape(table.caption)
    parts = ['<div class="table-wrap">']
    if caption:
        parts.append(f'<div class="table-caption">{caption}</div>')
    if not table.simple:
        parts.append(f'<pre class="code-block raw-table-source">{html.escape(table.flat_text)}</pre>')
        if table.raw_latex:
            parts.append('<details class="raw-table"><summary>View raw LaTeX</summary>')
            parts.append(f'<pre>{html.escape(table.raw_latex)}</pre></details>')
        parts.append('</div>')
        return "".join(parts)
    parts.append('<table class="latex-table"><tbody>')
    for pair in aligned:
        row = pair.old if side == "old" else pair.new
        row_status = "added" if pair.old is None else "deleted" if pair.new is None else ""
        parts.append(f'<tr class="{row_status}">')
        for column in range(columns):
            old_cell, new_cell, status = table_cell_status(pair.old, pair.new, column)
            value = old_cell if side == "old" else new_cell
            if value is None:
                parts.append('<td class="missing"></td>')
                continue
            if status == "modified" and old_cell is not None and new_cell is not None:
                old_html, new_html = token_diff(old_cell, new_cell)
                cell_html = old_html if side == "old" else new_html
            else:
                cell_html = html.escape(value)
            parts.append(f'<td class="cell-{status}">{cell_html}</td>')
        parts.append('</tr>')
    parts.append('</tbody></table>')
    if not table.simple and table.raw_latex:
        parts.append('<details class="raw-table"><summary>Show raw LaTeX</summary>')
        parts.append(f'<pre>{html.escape(table.raw_latex)}</pre></details>')
    parts.append('</div>')
    return "".join(parts)
