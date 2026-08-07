"""Domain models used by extraction, comparison, and rendering."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


class BlockKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list-item"
    QUOTE = "quote"
    MATH = "math"
    TABLE = "table"
    CODE = "code"


class ChangeKind(str, Enum):
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    MOVED = "moved"


MathMode = Literal["keep", "placeholder", "drop"]
ExtractorMode = Literal["auto", "pandoc", "builtin"]
PdfEngine = Literal["auto", "reportlab", "weasyprint", "playwright", "chromium"]


@dataclass(frozen=True, slots=True)
class ExtractionOptions:
    extractor: ExtractorMode = "auto"
    # Keep formula source by default so formula-only edits remain visible.
    math_mode: MathMode = "keep"
    math_placeholder: str = "[MATH]"
    ignore_citations: bool = True
    ignore_references: bool = True
    expand_inputs: bool = True
    include_code: bool = False


@dataclass(frozen=True, slots=True)
class TableData:
    """A best-effort logical table extracted from LaTeX or Pandoc."""

    rows: tuple[tuple[str, ...], ...]
    caption: str = ""
    raw_latex: str = ""
    simple: bool = True

    @property
    def column_count(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    @property
    def flat_text(self) -> str:
        body = "\n".join(" | ".join(cell for cell in row) for row in self.rows)
        return f"{self.caption}\n{body}".strip()


@dataclass(frozen=True, slots=True)
class TextBlock:
    index: int
    kind: BlockKind
    text: str
    level: int = 0
    anchor: str | None = None
    raw_latex: str | None = None
    table: TableData | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass(slots=True)
class DiffRow:
    old: TextBlock | None
    new: TextBlock | None
    change: ChangeKind
    similarity: float = 0.0
    old_html: str = ""
    new_html: str = ""
    visible: bool = True
    movement_note: str | None = None


@dataclass(frozen=True, slots=True)
class DiffStats:
    old_blocks: int
    new_blocks: int
    unchanged: int
    modified: int
    added: int
    deleted: int
    moved: int


@dataclass(slots=True)
class DiffReport:
    old_path: Path
    new_path: Path
    rows: list[DiffRow]
    stats: DiffStats
    extractor_used: str
    warnings: list[str] = field(default_factory=list)
    html_path: Path | None = None
    pdf_path: Path | None = None
    pdf_engine: str | None = None
    old_label: str | None = None
    new_label: str | None = None
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def display_old(self) -> str:
        return self.old_label or self.old_path.name

    @property
    def display_new(self) -> str:
        return self.new_label or self.new_path.name
