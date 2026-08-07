"""Pandoc AST and built-in LaTeX extraction with formula/table preservation."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata
from typing import Any, Iterable

from .models import BlockKind, ExtractionOptions, TableData, TextBlock
from .source import read_tex_source


class ExtractionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    blocks: list[TextBlock]
    backend: str
    warnings: list[str]


_DROP_ENVIRONMENTS = {
    "figure", "figure*", "tikzpicture", "picture", "thebibliography", "bibliography"
}
_RAW_ENVIRONMENTS = {"minipage", "subfigure", "subtable"}
_CODE_ENVIRONMENTS = {"verbatim", "Verbatim", "lstlisting", "minted"}
_MATH_ENVIRONMENTS = {
    "equation", "equation*", "align", "align*", "alignat", "alignat*", "gather",
    "gather*", "multline", "multline*", "displaymath", "math", "split", "cases",
}
_TABLE_ENVIRONMENTS = {"table", "table*", "tabular", "tabular*", "tabularx", "longtable"}
_FORMATTING_COMMANDS = {
    "textbf", "textit", "emph", "underline", "textrm", "textsf", "texttt", "textsc",
    "mbox", "makebox", "framebox", "parbox", "raisebox",
    "MakeUppercase", "MakeLowercase", "footnote", "url", "path",
}
_DROP_COMMANDS_WITH_ARG = {
    "label", "index", "footnotemark", "bibliography", "bibliographystyle",
    "includegraphics", "input", "include",
}
_CITE_COMMANDS = {
    "cite", "citep", "citet", "citealp", "citeauthor", "citeyear", "parencite",
    "textcite", "autocite",
}
_REF_COMMANDS = {"ref", "pageref", "autoref", "cref", "Cref", "eqref"}
_SENTINEL_RE = re.compile(r"@@TEXDIFF_BLOCK_(\d+)@@")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("~", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    return re.sub(r"\s*\n\s*", " ", text).strip()


def _normalize_math(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"%.*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_comments(source: str) -> str:
    lines: list[str] = []
    for line in source.splitlines():
        out: list[str] = []
        backslashes = 0
        for char in line:
            if char == "%" and backslashes % 2 == 0:
                break
            out.append(char)
            backslashes = backslashes + 1 if char == "\\" else 0
        lines.append("".join(out))
    return "\n".join(lines)


def _balanced_group(text: str, start: int) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    escaped = False
    for idx in range(start, len(text)):
        char = text[idx]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:idx], idx + 1
    return None


def _replace_command_group(source: str, command: str, replacement: str | None) -> str:
    pattern = re.compile(rf"\\{re.escape(command)}\*?(?:\s*\[[^\]]*\])?\s*\{{")
    cursor = 0
    result: list[str] = []
    while True:
        match = pattern.search(source, cursor)
        if not match:
            result.append(source[cursor:])
            break
        result.append(source[cursor:match.start()])
        parsed = _balanced_group(source, match.end() - 1)
        if parsed is None:
            result.append(source[match.start():match.end()])
            cursor = match.end()
            continue
        content, end = parsed
        result.append(content if replacement is None else replacement)
        cursor = end
    return "".join(result)


def _replace_inline_math(source: str, options: ExtractionOptions) -> str:
    replacement = options.math_placeholder if options.math_mode == "placeholder" else ""
    if options.math_mode in {"drop", "placeholder"}:
        source = re.sub(r"\\\(.*?\\\)", replacement, source, flags=re.DOTALL)
        source = re.sub(r"(?<!\\)\$\$.*?(?<!\\)\$\$", replacement, source, flags=re.DOTALL)
        source = re.sub(r"(?<!\\)\$(?:\\.|[^$])*?(?<!\\)\$", replacement, source, flags=re.DOTALL)
        return re.sub(r"\\\[.*?\\\]", replacement, source, flags=re.DOTALL)

    def plain_math(match: re.Match[str]) -> str:
        return rf"\({_normalize_math(match.group(1))}\)"

    source = re.sub(r"\\\((.*?)\\\)", plain_math, source, flags=re.DOTALL)
    source = re.sub(r"(?<!\\)\$(?!\$)((?:\\.|[^$])*?)(?<!\\)\$", plain_math, source, flags=re.DOTALL)
    return re.sub(r"\\\[(.*?)\\\]", lambda m: rf"\\[{_normalize_math(m.group(1))}\\]", source, flags=re.DOTALL)


def _remove_graphics_macros(source: str) -> str:
    pattern = re.compile(r"\\(?:onecolfig|twocolfig)\s*\{")
    cursor = 0
    result: list[str] = []
    while match := pattern.search(source, cursor):
        result.append(source[cursor:match.start()])
        # Start at the opening brace consumed by the regex, then consume
        # all three arguments ({width}{label}{caption}).
        pos = match.end() - 1
        for _ in range(3):
            parsed = _balanced_group(source, pos)
            if parsed is None:
                break
            _, pos = parsed
            # Skip whitespace between arguments
            while pos < len(source) and source[pos] in ' \t\n\r':
                pos += 1
            if pos >= len(source) or source[pos] != '{':
                break
        result.append("\n")
        cursor = pos
    result.append(source[cursor:])
    return "".join(result)


def _decode_latex_text(source: str, options: ExtractionOptions) -> str:
    source = _remove_graphics_macros(source)
    source = _replace_inline_math(source, options)
    inline_math: list[str] = []

    def protect_math(match: re.Match[str]) -> str:
        inline_math.append(match.group(0))
        return f"@@TEXDIFF_INLINE_MATH_{len(inline_math) - 1}@@"

    source = re.sub(r"\\\(.*?\\\)", protect_math, source, flags=re.DOTALL)
    source = re.sub(r"\\\[.*?\\\]", protect_math, source, flags=re.DOTALL)
    for command in _FORMATTING_COMMANDS:
        source = _replace_command_group(source, command, None)
    for command in _DROP_COMMANDS_WITH_ARG:
        source = _replace_command_group(source, command, "")
    for command in _CITE_COMMANDS:
        source = _replace_command_group(source, command, "" if options.ignore_citations else "[CITATION]")
    for command in _REF_COMMANDS:
        source = _replace_command_group(source, command, "" if options.ignore_references else "[REFERENCE]")
    replacements = {
        r"\%": "%", r"\&": "&", r"\_": "_", r"\#": "#", r"\$": "$",
        r"\{": "{", r"\}": "}", "---": "—", "--": "–", "``": "“", "''": "”",
        r"\\": "\n",
    }
    for old, new in replacements.items():
        source = source.replace(old, new)
    source = re.sub(r"\\[a-zA-Z@]+\*?(?:\s*\[[^\]]*\])*", " ", source)
    source = re.sub(r"[{}]", "", source)
    for index, value in enumerate(inline_math):
        source = source.replace(f"@@TEXDIFF_INLINE_MATH_{index}@@", value)
    return source


def _document_body(source: str) -> str:
    begin = re.search(r"\\begin\s*\{document\}", source)
    if begin:
        source = source[begin.end():]
    end = re.search(r"\\end\s*\{document\}", source)
    return source[:end.start()] if end else source


def _find_environment_end(source: str, env: str, start: int) -> tuple[int, int] | None:
    pattern = re.compile(rf"\\(begin|end)\s*\{{{re.escape(env)}\}}")
    depth = 1
    for match in pattern.finditer(source, start):
        depth += 1 if match.group(1) == "begin" else -1
        if depth == 0:
            return match.start(), match.end()
    return None


def _split_tex(text: str, delimiter: str) -> list[str]:
    """Split at top-level ``&`` or ``\\\\`` while respecting braces and escapes."""
    result: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "{" and (i == 0 or text[i - 1] != "\\"):
            depth += 1
        elif char == "}" and (i == 0 or text[i - 1] != "\\"):
            depth = max(0, depth - 1)
        if depth == 0 and delimiter == "&" and char == "&" and (i == 0 or text[i - 1] != "\\"):
            result.append("".join(current))
            current = []
            i += 1
            continue
        if depth == 0 and delimiter == r"\\" and text.startswith(r"\\", i):
            result.append("".join(current))
            current = []
            i += 2
            if i < len(text) and text[i] == "[":
                close = text.find("]", i + 1)
                if close != -1:
                    i = close + 1
            continue
        current.append(char)
        i += 1
    result.append("".join(current))
    return result


def _tabular_bounds(raw: str) -> tuple[str, int, str] | None:
    match = re.search(r"\\begin\s*\{(tabular\*?|tabularx|longtable)\}", raw)
    if not match:
        return None
    cursor = match.end()
    groups: list[str] = []
    while len(groups) < 2:
        while cursor < len(raw) and raw[cursor].isspace():
            cursor += 1
        if cursor >= len(raw) or raw[cursor] != "{":
            break
        parsed = _balanced_group(raw, cursor)
        if parsed is None:
            break
        content, cursor = parsed
        groups.append(content)
    column_spec = groups[-1] if groups else ""
    return match.group(1), cursor, column_spec


def _expand_multicolumn(source: str) -> str:
    pattern = re.compile(r"\\multicolumn\s*\{")
    cursor = 0
    result: list[str] = []
    while match := pattern.search(source, cursor):
        result.append(source[cursor:match.start()])
        count_group = _balanced_group(source, match.end() - 1)
        if count_group is None:
            result.append(source[match.start():match.end()])
            cursor = match.end()
            continue
        count_text, next_cursor = count_group
        while next_cursor < len(source) and source[next_cursor].isspace():
            next_cursor += 1
        align_group = _balanced_group(source, next_cursor)
        if align_group is None:
            result.append(source[match.start():match.end()])
            cursor = match.end()
            continue
        _, next_cursor = align_group
        while next_cursor < len(source) and source[next_cursor].isspace():
            next_cursor += 1
        content_group = _balanced_group(source, next_cursor)
        if content_group is None:
            result.append(source[match.start():match.end()])
            cursor = match.end()
            continue
        content, cursor = content_group
        try:
            count = max(1, int(count_text.strip()))
        except ValueError:
            count = 1
        result.append(content + " & " * (count - 1))
    result.append(source[cursor:])
    return "".join(result)


def _parse_builtin_table(raw: str, options: ExtractionOptions) -> TableData:
    caption_match = re.search(r"\\caption(?:\[[^\]]*\])?\s*\{", raw)
    caption = ""
    if caption_match:
        parsed = _balanced_group(raw, caption_match.end() - 1)
        if parsed:
            caption = _normalize(_decode_latex_text(parsed[0], options))

    bounds = _tabular_bounds(raw)
    if not bounds:
        fallback = _normalize(_decode_latex_text(raw, options))
        return TableData(((fallback or "Complex LaTeX table",),), caption, raw, False)
    env, body_start, column_spec = bounds
    end_match = re.search(rf"\\end\s*\{{{re.escape(env)}\}}", raw[body_start:])
    if not end_match:
        fallback = _normalize(_decode_latex_text(raw, options))
        return TableData(((fallback or "Complex LaTeX table",),), caption, raw, False)
    body = raw[body_start:body_start + end_match.start()]
    body = _strip_comments(body)
    body = re.sub(r"\\(?:toprule|midrule|bottomrule|hline|cline\{[^}]*\}|cmidrule(?:\([^)]*\))?\{[^}]*\})", "", body)
    body = _expand_multicolumn(body)
    body = re.sub(r"(?m)^\s*\\(?:addlinespace|noalign)\b[^\n]*", "", body)
    rows: list[tuple[str, ...]] = []
    for raw_row in _split_tex(body, r"\\"):
        cells = tuple(
            _normalize(_decode_latex_text(cell, options))
            for cell in _split_tex(raw_row, "&")
        )
        if any(cells):
            rows.append(cells)
    if not rows:
        fallback = _normalize(_decode_latex_text(body, options))
        rows = [(fallback or "Complex LaTeX table",)]
    # Strip @{} column spec modifiers — they are purely formatting (spacing/rule
    # insertion) and do not affect cell content.  Only mark as non-simple when
    # the spec contains actual per-cell content directives embedded via >{...}.
    clean_spec = re.sub(r"@\{[^}]*\}", "", column_spec) if column_spec else ""
    simple = not (">{" in clean_spec)
    return TableData(tuple(rows), caption, raw, simple)


def _protect_special_blocks(source: str, options: ExtractionOptions) -> tuple[str, list[TextBlock]]:
    begin_re = re.compile(r"\\begin\s*\{([^{}]+)\}")
    cursor = 0
    out: list[str] = []
    special: list[TextBlock] = []
    while True:
        match = begin_re.search(source, cursor)
        if not match:
            out.append(source[cursor:])
            break
        env = match.group(1)
        category = (
            "drop" if env in _DROP_ENVIRONMENTS else
            "raw" if env in _RAW_ENVIRONMENTS else
            "table" if env in _TABLE_ENVIRONMENTS else
            "math" if env in _MATH_ENVIRONMENTS else
            "code" if env in _CODE_ENVIRONMENTS else None
        )
        if category is None:
            out.append(source[cursor:match.end()])
            cursor = match.end()
            continue
        found = _find_environment_end(source, env, match.end())
        if found is None:
            out.append(source[cursor:match.end()])
            cursor = match.end()
            continue
        end_start, end_end = found
        raw = source[match.start():end_end]
        inner = source[match.end():end_start]
        out.append(source[cursor:match.start()])
        if category == "table":
            table = _parse_builtin_table(raw, options)
            block = TextBlock(-1, BlockKind.TABLE, table.flat_text, raw_latex=raw, table=table)
            special.append(block)
            out.append(f"\n\n@@TEXDIFF_BLOCK_{len(special)-1}@@\n\n")
        elif category == "raw":
            value = raw.strip()
            special.append(TextBlock(-1, BlockKind.CODE, value, raw_latex=raw))
            out.append(f"\n\n@@TEXDIFF_BLOCK_{len(special)-1}@@\n\n")
        elif category == "math" and options.math_mode != "drop":
            value = options.math_placeholder if options.math_mode == "placeholder" else _normalize_math(inner)
            special.append(TextBlock(-1, BlockKind.MATH, value, raw_latex=raw))
            out.append(f"\n\n@@TEXDIFF_BLOCK_{len(special)-1}@@\n\n")
        elif category == "code" and options.include_code:
            special.append(TextBlock(-1, BlockKind.CODE, inner.strip(), raw_latex=raw))
            out.append(f"\n\n@@TEXDIFF_BLOCK_{len(special)-1}@@\n\n")
        else:
            out.append("\n\n")
        cursor = end_end
    return "".join(out), special


def extract_with_builtin(source: str, options: ExtractionOptions) -> list[TextBlock]:
    source = _strip_comments(_document_body(source))
    source, special = _protect_special_blocks(source, options)
    heading_re = re.compile(
        r"\\(part|chapter|section|subsection|subsubsection|paragraph|subparagraph)"
        r"\*?(?:\s*\[[^\]]*\])?\s*\{"
    )
    levels = {
        "part": 1, "chapter": 1, "section": 2, "subsection": 3,
        "subsubsection": 4, "paragraph": 5, "subparagraph": 6,
    }
    chunks: list[tuple[str, str, int]] = []
    cursor = 0
    while True:
        match = heading_re.search(source, cursor)
        if not match:
            chunks.append(("body", source[cursor:], 0))
            break
        if match.start() > cursor:
            chunks.append(("body", source[cursor:match.start()], 0))
        parsed = _balanced_group(source, match.end() - 1)
        if parsed is None:
            cursor = match.end()
            continue
        title, end = parsed
        chunks.append(("heading", title, levels[match.group(1)]))
        cursor = end

    blocks: list[TextBlock] = []

    def add(block: TextBlock) -> None:
        blocks.append(
            TextBlock(
                len(blocks), block.kind, block.text, block.level, block.anchor,
                block.raw_latex, block.table,
            )
        )

    def add_body_piece(piece: str) -> None:
        if not piece.strip():
            return
        cursor2 = 0
        for marker in _SENTINEL_RE.finditer(piece):
            before = piece[cursor2:marker.start()]
            if before.strip():
                value = _normalize(before)
                if value:
                    add(TextBlock(-1, BlockKind.PARAGRAPH, value))
            index = int(marker.group(1))
            if 0 <= index < len(special):
                add(special[index])
            cursor2 = marker.end()
        after = piece[cursor2:]
        if after.strip():
            value = _normalize(after)
            if value:
                add(TextBlock(-1, BlockKind.PARAGRAPH, value))

    for kind, text, level in chunks:
        if kind == "heading":
            clean = _normalize(_decode_latex_text(text, options))
            if clean:
                add(TextBlock(-1, BlockKind.HEADING, clean, level=level))
            continue
        prepared = re.sub(r"\\begin\s*\{(?:itemize|enumerate|description|quote|quotation)\}", "\n", text)
        prepared = re.sub(r"\\end\s*\{(?:itemize|enumerate|description|quote|quotation)\}", "\n", prepared)
        prepared = re.sub(r"\\item(?:\s*\[[^\]]*\])?", "\n@@ITEM@@ ", prepared)
        clean = _decode_latex_text(prepared, options)
        for piece in re.split(r"\n\s*\n+", clean):
            parts = piece.split("@@ITEM@@")
            if len(parts) > 1:
                add_body_piece(parts[0])
                for item in parts[1:]:
                    # Keep special structural blocks in order even if embedded near a list.
                    markers = list(_SENTINEL_RE.finditer(item))
                    if markers:
                        add_body_piece(item)
                    else:
                        value = _normalize(item)
                        if value:
                            add(TextBlock(-1, BlockKind.LIST_ITEM, value))
            else:
                add_body_piece(piece)
    return blocks


def _math_text(math_type: dict[str, Any], value: str, options: ExtractionOptions) -> str:
    if options.math_mode == "drop":
        return ""
    if options.math_mode == "placeholder":
        return options.math_placeholder
    normalized = _normalize_math(value)
    return rf"\[{normalized}\]" if math_type.get("t") == "DisplayMath" else rf"\({normalized}\)"


def _stringify_inlines(inlines: Iterable[dict[str, Any]], options: ExtractionOptions) -> str:
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        tag = node.get("t")
        content = node.get("c")
        if tag == "Str":
            parts.append(str(content))
        elif tag in {"Space", "SoftBreak", "LineBreak"}:
            parts.append(" ")
        elif tag in {"Emph", "Strong", "Strikeout", "Superscript", "Subscript", "SmallCaps"}:
            walk(content)
        elif tag == "Quoted":
            quote_type, nested = content
            opening, closing = ("‘", "’") if quote_type.get("t") == "SingleQuote" else ("“", "”")
            parts.append(opening)
            walk(nested)
            parts.append(closing)
        elif tag == "Code" and options.include_code:
            parts.append(str(content[1]))
        elif tag == "Math":
            value = _math_text(content[0], str(content[1]), options)
            if value:
                parts.append(value)
        elif tag == "Cite":
            if not options.ignore_citations:
                parts.append("[CITATION]")
        elif tag == "Link":
            walk(content[1])
        elif tag == "Note":
            walk(content)
        elif isinstance(content, (list, dict)):
            walk(content)

    walk(list(inlines))
    return _normalize("".join(parts))


def _plain_from_blocks(blocks: Iterable[dict[str, Any]], options: ExtractionOptions) -> str:
    values: list[str] = []
    for block in blocks:
        tag = block.get("t")
        content = block.get("c")
        if tag in {"Para", "Plain"}:
            values.append(_stringify_inlines(content, options))
        elif tag == "CodeBlock" and options.include_code:
            values.append(str(content[1]))
        elif tag == "BlockQuote":
            values.append(_plain_from_blocks(content, options))
        elif tag == "Div":
            values.append(_plain_from_blocks(content[1], options))
    return _normalize(" ".join(value for value in values if value))


def _pandoc_row(row: Any, options: ExtractionOptions) -> tuple[str, ...]:
    try:
        cells = row[1]
    except (IndexError, TypeError):
        return ()
    result: list[str] = []
    for cell in cells:
        try:
            blocks = cell[4]
        except (IndexError, TypeError):
            result.append("")
        else:
            result.append(_plain_from_blocks(blocks, options))
    return tuple(result)


def _pandoc_table(content: Any, options: ExtractionOptions) -> TableData:
    """Parse the modern Pandoc Table AST; gracefully degrade for older shapes."""
    try:
        caption_data = content[1]
        caption = _plain_from_blocks(caption_data[1], options) if caption_data and len(caption_data) > 1 else ""
        head = content[3]
        bodies = content[4]
        foot = content[5]
        rows: list[tuple[str, ...]] = []
        for row in head[1]:
            parsed = _pandoc_row(row, options)
            if parsed:
                rows.append(parsed)
        for body in bodies:
            for row in [*body[2], *body[3]]:
                parsed = _pandoc_row(row, options)
                if parsed:
                    rows.append(parsed)
        for row in foot[1]:
            parsed = _pandoc_row(row, options)
            if parsed:
                rows.append(parsed)
        if rows:
            return TableData(tuple(rows), caption=caption, simple=True)
    except (IndexError, TypeError, AttributeError):
        pass
    return TableData((("Complex table (Pandoc AST could not be simplified)",),), simple=False)


def _pandoc_blocks(ast_blocks: list[dict[str, Any]], options: ExtractionOptions) -> list[TextBlock]:
    blocks: list[TextBlock] = []

    def add(
        kind: BlockKind,
        text: str,
        level: int = 0,
        anchor: str | None = None,
        *,
        raw_latex: str | None = None,
        table: TableData | None = None,
    ) -> None:
        text = _normalize(text) if kind != BlockKind.MATH else _normalize_math(text)
        if text:
            blocks.append(TextBlock(len(blocks), kind, text, level, anchor or None, raw_latex, table))

    def add_para(inlines: list[dict[str, Any]], inherited: BlockKind) -> None:
        current: list[dict[str, Any]] = []
        for inline in inlines:
            if inline.get("t") == "Math" and inline.get("c", [{"t": ""}])[0].get("t") == "DisplayMath":
                if current:
                    add(inherited, _stringify_inlines(current, options))
                    current = []
                if options.math_mode != "drop":
                    source = str(inline["c"][1])
                    value = options.math_placeholder if options.math_mode == "placeholder" else source
                    add(BlockKind.MATH, value, raw_latex=source)
            else:
                current.append(inline)
        if current:
            add(inherited, _stringify_inlines(current, options))

    def walk(items: Iterable[dict[str, Any]], inherited: BlockKind = BlockKind.PARAGRAPH) -> None:
        for block in items:
            tag = block.get("t")
            content = block.get("c")
            if tag in {"Para", "Plain"}:
                add_para(content, inherited)
            elif tag == "Header":
                level, attr, inlines = content
                add(BlockKind.HEADING, _stringify_inlines(inlines, options), int(level), attr[0] if attr else None)
            elif tag == "BlockQuote":
                walk(content, BlockKind.QUOTE)
            elif tag in {"BulletList", "OrderedList"}:
                list_items = content if tag == "BulletList" else content[1]
                for item in list_items:
                    add(BlockKind.LIST_ITEM, _plain_from_blocks(item, options))
            elif tag == "DefinitionList":
                for term, definitions in content:
                    term_text = _stringify_inlines(term, options)
                    for definition in definitions:
                        add(BlockKind.LIST_ITEM, f"{term_text}: {_plain_from_blocks(definition, options)}")
            elif tag == "Div":
                walk(content[1], inherited)
            elif tag == "CodeBlock" and options.include_code:
                add(BlockKind.CODE, content[1], raw_latex=content[1])
            elif tag == "Table":
                table = _pandoc_table(content, options)
                add(BlockKind.TABLE, table.flat_text, table=table)

    walk(ast_blocks)
    return blocks


def extract_with_pandoc(source: str, options: ExtractionOptions, *, pandoc: str = "pandoc") -> list[TextBlock]:
    result = subprocess.run(
        [pandoc, "--from=latex", "--to=json", "--wrap=none"],
        input=source,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ExtractionError(f"Pandoc extraction failed: {result.stderr.strip() or 'unknown Pandoc error'}")
    try:
        return _pandoc_blocks(json.loads(result.stdout)["blocks"], options)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ExtractionError("Pandoc returned invalid JSON") from exc


def extract_source(source: str, options: ExtractionOptions, *, label: str = "source") -> ExtractionResult:
    warnings: list[str] = []
    # The native extractor is now the automatic fast path. It preserves formula
    # source and table grids and avoids two external Pandoc processes per report.
    if options.extractor in {"auto", "builtin"}:
        blocks = extract_with_builtin(source, options)
        if blocks:
            return ExtractionResult(blocks, "builtin", warnings)
        if options.extractor == "builtin":
            warnings.append(f"No comparable content was found in {label}.")
            return ExtractionResult([], "builtin", warnings)
        warnings.append(f"The built-in extractor found no comparable blocks for {label}; tried Pandoc.")

    pandoc = shutil.which("pandoc")
    if not pandoc:
        if options.extractor == "pandoc":
            raise ExtractionError("Pandoc was requested but is not installed or not on PATH.")
        warnings.append("Pandoc was not found and the built-in extractor returned no content.")
        return ExtractionResult([], "builtin", warnings)
    try:
        blocks = extract_with_pandoc(source, options, pandoc=pandoc)
    except ExtractionError:
        if options.extractor == "pandoc":
            raise
        raise
    if not blocks:
        warnings.append(f"No comparable content was found in {label}.")
    return ExtractionResult(blocks, "pandoc", warnings)


def extract_document(path: Path, options: ExtractionOptions) -> ExtractionResult:
    source = read_tex_source(path, expand_inputs=options.expand_inputs)
    return extract_source(source, options, label=str(path))
