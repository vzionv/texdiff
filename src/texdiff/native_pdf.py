"""Cross-platform native PDF renderer implemented with ReportLab."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable

from .models import BlockKind, ChangeKind, DiffReport, DiffRow
from .table_diff import AlignedTableRow, align_table_rows, table_cell_status
from .theme import Theme, load_theme

_TOKEN_RE = re.compile(
    r"\r\n|\r|\n|[\t ]+|"
    r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]|"
    r"[\w]+(?:['’-][\w]+)*|[^\w\s]",
    re.UNICODE,
)


class NativePdfError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Fragment:
    text: str
    style: str = "normal"


class _InlineParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[Fragment] = []
        self._styles: list[str] = ["normal"]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "del":
            self._styles.append("deleted")
        elif tag == "ins":
            self._styles.append("added")
        elif tag in {"br", "tr"}:
            self.fragments.append(Fragment("\n", self._styles[-1]))
        elif tag in {"td", "th"} and self.fragments and not self.fragments[-1].text.endswith("\n"):
            self.fragments.append(Fragment(" | ", "muted"))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"del", "ins"} and len(self._styles) > 1:
            self._styles.pop()
        elif tag in {"div", "pre"}:
            self.fragments.append(Fragment("\n", self._styles[-1]))

    def handle_data(self, data: str) -> None:
        if data:
            self.fragments.append(Fragment(data, self._styles[-1]))


@lru_cache(maxsize=8192)
def _parse_inline(value: str) -> tuple[Fragment, ...]:
    parser = _InlineParser()
    parser.feed(value)
    parser.close()
    return tuple(parser.fragments)


def _report_text(report: DiffReport) -> str:
    values = [report.display_old, report.display_new]
    for row in report.rows:
        if row.old:
            values.append(row.old.text)
        if row.new:
            values.append(row.new.text)
        if row.movement_note:
            values.append(row.movement_note)
    return "\n".join(values)


def _font_candidates() -> Iterable[Path]:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    home = Path.home()
    yield from (
        windir / "Fonts" / "msyh.ttc",
        windir / "Fonts" / "msyh.ttf",
        windir / "Fonts" / "simhei.ttf",
        windir / "Fonts" / "simsun.ttc",
        windir / "Fonts" / "arialuni.ttf",
        local / "Microsoft" / "Windows" / "Fonts" / "msyh.ttc",
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        home / ".local" / "share" / "fonts" / "NotoSansCJK-Regular.ttc",
    )


def _register_font(text: str) -> str:
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as exc:
        raise NativePdfError("ReportLab is not installed") from exc

    has_korean = bool(re.search(r"[\uAC00-\uD7AF]", text))
    has_japanese = bool(re.search(r"[\u3040-\u30FF]", text))
    has_chinese = bool(re.search(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text))
    if has_korean or has_japanese or has_chinese:
        cid_name = "HYSMyeongJo-Medium" if has_korean else "HeiseiMin-W3" if has_japanese else "STSong-Light"
        if cid_name not in pdfmetrics.getRegisteredFontNames():
            try:
                pdfmetrics.registerFont(UnicodeCIDFont(cid_name))
            except Exception as exc:
                raise NativePdfError(f"Could not register ReportLab CJK font {cid_name}: {exc}") from exc
        return cid_name

    font_name = "TexDiffLatin"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    for path in _font_candidates():
        if not path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(font_name, str(path), subfontIndex=0))
            return font_name
        except Exception:
            continue
    return "Helvetica"


@lru_cache(maxsize=100_000)
def _string_width(text: str, font_name: str, font_size: float) -> float:
    from reportlab.pdfbase import pdfmetrics

    return pdfmetrics.stringWidth(text, font_name, font_size)


def _tokenize(fragments: Iterable[Fragment]) -> Iterable[Fragment]:
    for fragment in fragments:
        for token in _TOKEN_RE.findall(fragment.text):
            yield Fragment(token, fragment.style)


def _wrap_fragments(
    fragments: Iterable[Fragment],
    *,
    width: float,
    font_name: str,
    font_size: float,
) -> list[list[Fragment]]:
    """Wrap fragments in linear time.

    A repeated ``list.pop(0)`` call becomes quadratic for long documents.
    A deque plus cached glyph widths keeps wrapping linear.
    """
    lines: list[list[Fragment]] = [[]]
    line_width = 0.0
    queue: deque[Fragment] = deque(_tokenize(fragments))
    while queue:
        token = queue.popleft()
        if token.text in {"\r", "\n", "\r\n"}:
            lines.append([])
            line_width = 0.0
            continue
        if token.text.isspace() and not lines[-1]:
            continue
        token_width = _string_width(token.text, font_name, font_size)
        if token_width > width and len(token.text) > 1:
            queue.extendleft(reversed([Fragment(char, token.style) for char in token.text]))
            continue
        if lines[-1] and line_width + token_width > width:
            lines.append([])
            line_width = 0.0
            if token.text.isspace():
                continue
        lines[-1].append(token)
        line_width += token_width
    if len(lines) > 1 and not lines[-1]:
        lines.pop()
    return lines or [[]]


def _row_font_size(row: DiffRow, theme: Theme) -> float:
    blocks = [block for block in (row.old, row.new) if block]
    if any(block.kind == BlockKind.HEADING and block.level <= 1 for block in blocks):
        return theme.pdf.title_font_size
    if any(block.kind == BlockKind.HEADING for block in blocks):
        return theme.pdf.heading_font_size
    if any(block.kind in {BlockKind.TABLE, BlockKind.CODE, BlockKind.MATH} for block in blocks):
        return max(6.6, theme.pdf.body_font_size - 0.4)
    return theme.pdf.body_font_size


def _cell_fragments(row: DiffRow, side: str) -> list[Fragment]:
    block = row.old if side == "old" else row.new
    if block is None:
        return [Fragment("<empty>", "muted")]
    value = row.old_html if side == "old" else row.new_html
    fragments = list(_parse_inline(value))
    if side == "new" and row.change == ChangeKind.MOVED and row.movement_note:
        fragments = [Fragment(f"[{row.movement_note}]\n", "muted"), *fragments]
    if block.kind == BlockKind.LIST_ITEM:
        fragments = [Fragment("- ", "muted"), *fragments]
    return fragments


def _token_diff_fragments(old: str, new: str) -> tuple[list[Fragment], list[Fragment]]:
    old_tokens = _TOKEN_RE.findall(old)
    new_tokens = _TOKEN_RE.findall(new)
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    old_result: list[Fragment] = []
    new_result: list[Fragment] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_text = "".join(old_tokens[i1:i2])
        new_text = "".join(new_tokens[j1:j2])
        if tag == "equal":
            old_result.append(Fragment(old_text))
            new_result.append(Fragment(new_text))
        elif tag == "delete":
            old_result.append(Fragment(old_text, "deleted"))
        elif tag == "insert":
            new_result.append(Fragment(new_text, "added"))
        else:
            old_result.append(Fragment(old_text, "deleted"))
            new_result.append(Fragment(new_text, "added"))
    return old_result, new_result


def _table_fragments(pair: AlignedTableRow, column: int, side: str) -> list[Fragment]:
    old_cell, new_cell, status = table_cell_status(pair.old, pair.new, column)
    value = old_cell if side == "old" else new_cell
    if value is None:
        return []
    if status == "modified" and old_cell is not None and new_cell is not None:
        old_fragments, new_fragments = _token_diff_fragments(old_cell, new_cell)
        return old_fragments if side == "old" else new_fragments
    if status == "added":
        return [Fragment(value, "added")]
    if status == "deleted":
        return [Fragment(value, "deleted")]
    return [Fragment(value)]


def _hex(value: str):
    from reportlab.lib.colors import HexColor

    return HexColor(value)


def _cell_background(change: ChangeKind, side: str, theme: Theme):
    colors = theme.colors
    if change == ChangeKind.UNCHANGED:
        return _hex(colors.page_background)
    if change == ChangeKind.MODIFIED:
        return _hex(colors.deleted_background if side == "old" else colors.added_background)
    if change == ChangeKind.ADDED and side == "new":
        return _hex(colors.added_background)
    if change == ChangeKind.DELETED and side == "old":
        return _hex(colors.deleted_background)
    if change == ChangeKind.MOVED:
        return _hex(colors.moved_background)
    return _hex(colors.page_background)


def _draw_line(
    canvas,
    fragments: list[Fragment],
    *,
    x: float,
    baseline: float,
    font_name: str,
    font_size: float,
    theme: Theme,
) -> None:
    colors = theme.colors
    cursor = x
    canvas.setFont(font_name, font_size)
    for fragment in fragments:
        if not fragment.text:
            continue
        width = _string_width(fragment.text, font_name, font_size)
        if fragment.style == "added":
            canvas.setFillColor(_hex(colors.added_strong))
            canvas.rect(cursor, baseline - 1.4, width, font_size + 2.2, stroke=0, fill=1)
        elif fragment.style == "deleted":
            canvas.setFillColor(_hex(colors.deleted_strong))
            canvas.rect(cursor, baseline - 1.4, width, font_size + 2.2, stroke=0, fill=1)
        canvas.setFillColor(_hex(colors.muted if fragment.style == "muted" else colors.text))
        canvas.drawString(cursor, baseline, fragment.text)
        if fragment.style == "deleted":
            canvas.setStrokeColor(_hex(colors.deleted_strong))
            canvas.setLineWidth(0.45)
            canvas.line(cursor, baseline + font_size * 0.32, cursor + width, baseline + font_size * 0.32)
        cursor += width


def write_native_pdf(
    report: DiffReport,
    pdf_path: Path,
    *,
    include_hidden_unchanged: bool = False,
    theme_path: str | Path | None = None,
) -> Path:
    try:
        from reportlab.lib.pagesizes import A4, landscape, portrait
        from reportlab.pdfgen.canvas import Canvas
    except ImportError as exc:
        raise NativePdfError("ReportLab is not installed. Install with: pip install texdiff") from exc

    theme = load_theme(theme_path)
    font_name = _register_font(_report_text(report))
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="texdiff-", suffix=".pdf", dir=pdf_path.parent)
    os.close(fd)
    temp_path = Path(temp_name)

    if theme.pdf.page_size.upper() != "A4":
        raise NativePdfError(f"Unsupported native PDF page size: {theme.pdf.page_size}")
    page_width, page_height = landscape(A4) if theme.pdf.orientation == "landscape" else portrait(A4)
    margin_x = theme.pdf.margin_points
    margin_bottom = theme.pdf.margin_points
    line_number_width = 24.0
    content_width = page_width - 2 * margin_x
    text_column_width = (content_width - 2 * line_number_width) / 2
    old_number_x = margin_x
    old_cell_x = old_number_x + line_number_width
    new_number_x = old_cell_x + text_column_width
    new_cell_x = new_number_x + line_number_width
    cell_padding = 5.0
    body_text_width = text_column_width - 2 * cell_padding
    colors = theme.colors
    border = _hex(colors.border)
    panel = _hex(colors.panel)
    muted = _hex(colors.muted)
    text_color = _hex(colors.text)

    canvas = Canvas(str(temp_path), pagesize=(page_width, page_height), pageCompression=1)
    canvas.setTitle(f"LaTeX diff: {report.display_old} vs {report.display_new}")
    canvas.setAuthor("texdiff")
    page_number = 0
    current_y = 0.0
    body_bottom = margin_bottom + 9.0

    def draw_footer() -> None:
        canvas.setFillColor(muted)
        canvas.setFont(font_name, 6.8)
        canvas.drawRightString(page_width - margin_x, 10.5, f"Page {page_number}")

    def start_page(first: bool) -> None:
        nonlocal page_number, current_y
        if page_number:
            draw_footer()
            canvas.showPage()
        page_number += 1
        top = page_height - 20.0
        canvas.setFillColor(text_color)
        canvas.setFont(font_name, 12.0 if first else 9.5)
        canvas.drawString(margin_x, top, f"LaTeX diff: {report.display_old} vs {report.display_new}")
        y = top - (13.5 if first else 11.0)
        if first:
            stats = report.stats
            summary = (
                f"Modified {stats.modified}   Added {stats.added}   Deleted {stats.deleted}   "
                f"Moved {stats.moved}   Unchanged {stats.unchanged}"
            )
            canvas.setFillColor(panel)
            canvas.roundRect(margin_x, y - 2.0, content_width, 13.0, 3.0, stroke=0, fill=1)
            canvas.setFillColor(text_color)
            canvas.setFont(font_name, 7.0)
            canvas.drawString(margin_x + 6.0, y + 1.5, summary)
            y -= 11.0
        header_height = 17.0
        canvas.setFillColor(panel)
        canvas.rect(margin_x, y - header_height, content_width, header_height, stroke=0, fill=1)
        canvas.setStrokeColor(border)
        canvas.setLineWidth(0.5)
        canvas.rect(margin_x, y - header_height, content_width, header_height, stroke=1, fill=0)
        canvas.line(new_number_x, y - header_height, new_number_x, y)
        canvas.setFillColor(text_color)
        canvas.setFont(font_name, 7.6)
        canvas.drawString(old_cell_x + cell_padding, y - 11.5, report.display_old)
        canvas.drawString(new_cell_x + cell_padding, y - 11.5, report.display_new)
        current_y = y - header_height

    def draw_line_numbers(row: DiffRow, *, top: float, font_size: float, first_chunk: bool) -> None:
        if not first_chunk:
            return
        canvas.setFillColor(muted)
        canvas.setFont(font_name, 6.4)
        if row.old:
            canvas.drawRightString(old_cell_x - 4.0, top - cell_padding - font_size, str(row.old.index + 1))
        if row.new:
            canvas.drawRightString(new_cell_x - 4.0, top - cell_padding - font_size, str(row.new.index + 1))

    def draw_text_chunk(
        row: DiffRow,
        old_lines: list[list[Fragment]],
        new_lines: list[list[Fragment]],
        *,
        offset: int,
        count: int,
        font_size: float,
        line_height: float,
    ) -> None:
        nonlocal current_y
        top = current_y
        row_height = count * line_height + 2 * cell_padding
        bottom = top - row_height
        canvas.setFillColor(panel)
        canvas.rect(old_number_x, bottom, line_number_width, row_height, stroke=0, fill=1)
        canvas.rect(new_number_x, bottom, line_number_width, row_height, stroke=0, fill=1)
        canvas.setFillColor(_cell_background(row.change, "old", theme))
        canvas.rect(old_cell_x, bottom, text_column_width, row_height, stroke=0, fill=1)
        canvas.setFillColor(_cell_background(row.change, "new", theme))
        canvas.rect(new_cell_x, bottom, text_column_width, row_height, stroke=0, fill=1)
        canvas.setStrokeColor(border)
        canvas.setLineWidth(0.35)
        canvas.line(margin_x, bottom, margin_x + content_width, bottom)
        for x in (old_cell_x, new_number_x, new_cell_x, margin_x + content_width):
            canvas.line(x, bottom, x, top)
        draw_line_numbers(row, top=top, font_size=font_size, first_chunk=offset == 0)
        for local_index in range(count):
            line_index = offset + local_index
            baseline = top - cell_padding - font_size - local_index * line_height
            if line_index < len(old_lines):
                _draw_line(canvas, old_lines[line_index], x=old_cell_x + cell_padding, baseline=baseline, font_name=font_name, font_size=font_size, theme=theme)
            if line_index < len(new_lines):
                _draw_line(canvas, new_lines[line_index], x=new_cell_x + cell_padding, baseline=baseline, font_name=font_name, font_size=font_size, theme=theme)
        current_y = bottom

    def draw_table_caption(row: DiffRow, font_size: float, line_height: float) -> None:
        nonlocal current_y
        old_caption = row.old.table.caption if row.old and row.old.table else ""
        new_caption = row.new.table.caption if row.new and row.new.table else ""
        if not old_caption and not new_caption:
            return
        old_fragments, new_fragments = _token_diff_fragments(old_caption, new_caption)
        old_lines = _wrap_fragments([Fragment("Table: ", "muted"), *old_fragments], width=body_text_width, font_name=font_name, font_size=font_size)
        new_lines = _wrap_fragments([Fragment("Table: ", "muted"), *new_fragments], width=body_text_width, font_name=font_name, font_size=font_size)
        total = max(len(old_lines), len(new_lines), 1)
        if current_y - (total * line_height + 2 * cell_padding) < body_bottom:
            start_page(False)
        draw_text_chunk(row, old_lines, new_lines, offset=0, count=total, font_size=font_size, line_height=line_height)

    def draw_table(row: DiffRow, font_size: float, line_height: float) -> None:
        nonlocal current_y
        old_table = row.old.table if row.old else None
        new_table = row.new.table if row.new else None
        if any(table and not table.simple for table in (old_table, new_table)):
            old_value = old_table.flat_text if old_table else "<empty>"
            new_value = new_table.flat_text if new_table else "<empty>"
            old_lines = _wrap_fragments([Fragment(old_value)], width=body_text_width, font_name=font_name, font_size=font_size)
            new_lines = _wrap_fragments([Fragment(new_value)], width=body_text_width, font_name=font_name, font_size=font_size)
            total_lines = max(len(old_lines), len(new_lines), 1)
            offset = 0
            while offset < total_lines:
                available = current_y - body_bottom - 2 * cell_padding
                lines_fit = max(1, int(available // line_height))
                count = min(total_lines - offset, lines_fit)
                draw_text_chunk(row, old_lines, new_lines, offset=offset, count=count, font_size=font_size, line_height=line_height)
                offset += count
                if offset < total_lines:
                    start_page(False)
            return
        draw_table_caption(row, font_size, line_height)
        aligned = align_table_rows(old_table, new_table)
        columns = max(old_table.column_count if old_table else 0, new_table.column_count if new_table else 0, 1)
        column_width = text_column_width / columns
        cell_text_width = max(8.0, column_width - 2 * 3.0)
        first_table_chunk = True
        for pair in aligned:
            old_cells = [
                _wrap_fragments(_table_fragments(pair, col, "old"), width=cell_text_width, font_name=font_name, font_size=font_size)
                for col in range(columns)
            ]
            new_cells = [
                _wrap_fragments(_table_fragments(pair, col, "new"), width=cell_text_width, font_name=font_name, font_size=font_size)
                for col in range(columns)
            ]
            total_lines = max([1, *(len(lines) for lines in old_cells), *(len(lines) for lines in new_cells)])
            offset = 0
            while offset < total_lines:
                available = current_y - body_bottom - 2 * 3.0
                lines_fit = int(available // line_height)
                if lines_fit < 1:
                    start_page(False)
                    continue
                count = min(total_lines - offset, lines_fit)
                top = current_y
                height = count * line_height + 2 * 3.0
                bottom = top - height
                canvas.setFillColor(panel)
                canvas.rect(old_number_x, bottom, line_number_width, height, stroke=0, fill=1)
                canvas.rect(new_number_x, bottom, line_number_width, height, stroke=0, fill=1)
                # Draw cells with cell-level status colors.
                for side, base_x in (("old", old_cell_x), ("new", new_cell_x)):
                    for col in range(columns):
                        old_cell, new_cell, status = table_cell_status(pair.old, pair.new, col)
                        if status == "modified":
                            fill = colors.deleted_background if side == "old" else colors.added_background
                        elif status == "deleted" and side == "old":
                            fill = colors.deleted_background
                        elif status == "added" and side == "new":
                            fill = colors.added_background
                        elif (old_cell if side == "old" else new_cell) is None:
                            fill = colors.panel_alt
                        else:
                            fill = colors.page_background
                        canvas.setFillColor(_hex(fill))
                        canvas.rect(base_x + col * column_width, bottom, column_width, height, stroke=0, fill=1)
                canvas.setStrokeColor(border)
                canvas.setLineWidth(0.35)
                for side_x in (old_cell_x, new_cell_x):
                    for col in range(columns + 1):
                        x = side_x + col * column_width
                        canvas.line(x, bottom, x, top)
                for x in (margin_x, old_cell_x, new_number_x, new_cell_x, margin_x + content_width):
                    canvas.line(x, bottom, x, top)
                canvas.line(margin_x, bottom, margin_x + content_width, bottom)
                draw_line_numbers(row, top=top, font_size=font_size, first_chunk=first_table_chunk)
                first_table_chunk = False
                for side, base_x, cells in (("old", old_cell_x, old_cells), ("new", new_cell_x, new_cells)):
                    del side
                    for col, lines in enumerate(cells):
                        for local in range(count):
                            line_index = offset + local
                            if line_index >= len(lines):
                                continue
                            baseline = top - 3.0 - font_size - local * line_height
                            _draw_line(canvas, lines[line_index], x=base_x + col * column_width + 3.0, baseline=baseline, font_name=font_name, font_size=font_size, theme=theme)
                current_y = bottom
                offset += count
                if offset < total_lines:
                    start_page(False)

    try:
        start_page(True)
        rows = [
            row for row in report.rows
            if include_hidden_unchanged
            or row.change != ChangeKind.UNCHANGED
            or any(block and block.kind == BlockKind.HEADING for block in (row.old, row.new))
        ]
        heading_positions = [
            (index, next(block.level for block in (row.old, row.new) if block and block.kind == BlockKind.HEADING))
            for index, row in enumerate(report.rows)
            if any(block and block.kind == BlockKind.HEADING for block in (row.old, row.new))
        ]
        if not include_hidden_unchanged and heading_positions:
            expanded: set[int] = set()
            section_sources = {
                index for index, row in enumerate(report.rows)
                if row.visible or row.change != ChangeKind.UNCHANGED
            }
            for index in section_sources:
                preceding = [(position, level) for position, level in heading_positions if position <= index]
                if not preceding:
                    continue
                start, level = preceding[-1]
                end = next(
                    (position for position, heading_level in heading_positions if position > start and heading_level <= level),
                    len(report.rows),
                )
                expanded.update(range(start, end))
            rows = [report.rows[index] for index in sorted(expanded)]
        if not rows:
            canvas.setFillColor(muted)
            canvas.setFont(font_name, 8.0)
            canvas.drawString(margin_x, current_y - 16.0, "No differences found.")
        for row in rows:
            font_size = _row_font_size(row, theme)
            line_height = font_size * theme.pdf.line_height_multiplier
            is_table = any(block and block.kind == BlockKind.TABLE for block in (row.old, row.new))
            if is_table:
                draw_table(row, font_size, line_height)
                continue
            old_lines = _wrap_fragments(_cell_fragments(row, "old"), width=body_text_width, font_name=font_name, font_size=font_size)
            new_lines = _wrap_fragments(_cell_fragments(row, "new"), width=body_text_width, font_name=font_name, font_size=font_size)
            total_lines = max(len(old_lines), len(new_lines), 1)
            offset = 0
            while offset < total_lines:
                available = current_y - body_bottom - 2 * cell_padding
                lines_fit = int(available // line_height)
                if lines_fit < 1:
                    start_page(False)
                    continue
                count = min(total_lines - offset, lines_fit)
                draw_text_chunk(row, old_lines, new_lines, offset=offset, count=count, font_size=font_size, line_height=line_height)
                offset += count
                if offset < total_lines:
                    start_page(False)

        if report.warnings:
            warning_text = "Warnings: " + " | ".join(report.warnings)
            warning_lines = _wrap_fragments([Fragment(warning_text, "muted")], width=content_width, font_name=font_name, font_size=6.6)
            needed = len(warning_lines) * 8.5 + 6
            if current_y - needed < body_bottom:
                start_page(False)
            for line in warning_lines:
                current_y -= 8.5
                _draw_line(canvas, line, x=margin_x, baseline=current_y, font_name=font_name, font_size=6.6, theme=theme)
        draw_footer()
        canvas.save()
        with temp_path.open("rb") as handle:
            if temp_path.stat().st_size < 100 or handle.read(5) != b"%PDF-":
                raise NativePdfError("ReportLab did not create a valid PDF")
        os.replace(temp_path, pdf_path)
        return pdf_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
