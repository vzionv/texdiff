"""Safe LaTeX source loading and local input expansion."""
from __future__ import annotations
import re
from pathlib import Path
_INPUT_RE = re.compile('(?<!\\\\)\\\\(?:input|include)\\s*\\{([^{}]+)\\}')

class SourceError(RuntimeError):
    pass

def _resolve_include(base_dir: Path, root: Path, raw_name: str) -> Path:
    candidate = (base_dir / raw_name.strip()).resolve()
    if not candidate.suffix:
        candidate = candidate.with_suffix('.tex')
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise SourceError(f'Included file escapes source directory: {raw_name}') from exc
    return candidate

def read_tex_source(path: Path, *, expand_inputs: bool=True, max_depth: int=20) -> str:
    path = path.resolve()
    root = path.parent
    seen: set[Path] = set()

    def read_one(current: Path, depth: int) -> str:
        if depth > max_depth:
            raise SourceError(f'Maximum include depth ({max_depth}) exceeded at {current}')
        if current in seen:
            raise SourceError(f'Circular LaTeX include detected at {current}')
        if not current.is_file():
            raise SourceError(f'LaTeX source not found: {current}')
        seen.add(current)
        try:
            text = current.read_text(encoding='utf-8-sig')
        except UnicodeDecodeError as exc:
            raise SourceError(f'Source is not UTF-8: {current}') from exc
        if not expand_inputs:
            seen.remove(current)
            return text

        def replace(match: re.Match[str]) -> str:
            included = _resolve_include(current.parent, root, match.group(1))
            return '\n' + read_one(included, depth + 1) + '\n'
        expanded_lines: list[str] = []
        for line in text.splitlines(keepends=True):
            comment_at: int | None = None
            slash_count = 0
            for index, char in enumerate(line):
                if char == '\\':
                    slash_count += 1
                    continue
                if char == '%' and slash_count % 2 == 0:
                    comment_at = index
                    break
                slash_count = 0
            if comment_at is None:
                expanded_lines.append(_INPUT_RE.sub(replace, line))
            else:
                code, comment = (line[:comment_at], line[comment_at:])
                expanded_lines.append(_INPUT_RE.sub(replace, code) + comment)
        seen.remove(current)
        return ''.join(expanded_lines)
    return read_one(path, 0)
