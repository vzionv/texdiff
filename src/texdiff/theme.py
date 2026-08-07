"""Load the developer-facing bundled theme configuration."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
import re

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


@dataclass(frozen=True, slots=True)
class Colors:
    page_background: str
    panel: str
    panel_alt: str
    border: str
    text: str
    muted: str
    added_background: str
    added_strong: str
    deleted_background: str
    deleted_strong: str
    modified_background: str
    moved_background: str
    search_highlight: str
    annotation_highlight: str
    focus: str


@dataclass(frozen=True, slots=True)
class HtmlTheme:
    font_size_px: int = 14
    line_height: float = 1.55
    sticky_header: bool = True
    default_view: str = "changes"


@dataclass(frozen=True, slots=True)
class PdfTheme:
    page_size: str = "A4"
    orientation: str = "landscape"
    body_font_size: float = 7.7
    heading_font_size: float = 8.8
    title_font_size: float = 9.6
    line_height_multiplier: float = 1.38
    margin_points: float = 24.0


@dataclass(frozen=True, slots=True)
class Theme:
    colors: Colors
    html: HtmlTheme
    pdf: PdfTheme


def _default_theme_path() -> Path:
    return Path(resources.files("texdiff").joinpath("assets/theme.toml"))


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_theme(theme: Theme) -> Theme:
    for name in theme.colors.__dataclass_fields__:
        value = getattr(theme.colors, name)
        if not _COLOR_RE.fullmatch(value):
            raise ValueError(f"Invalid texdiff theme color {name!r}: expected #RRGGBB")
    if not 8 <= theme.html.font_size_px <= 32 or not 0.8 <= theme.html.line_height <= 3.0:
        raise ValueError("Invalid texdiff HTML typography values")
    if theme.html.default_view not in {"changes", "context", "all", "unchanged"}:
        raise ValueError("Invalid texdiff HTML default_view")
    if theme.pdf.page_size != "A4" or theme.pdf.orientation not in {"portrait", "landscape"}:
        raise ValueError("Invalid texdiff PDF page settings")
    if not 4.0 <= theme.pdf.body_font_size <= 24.0 or not 4.0 <= theme.pdf.heading_font_size <= 32.0 or not 4.0 <= theme.pdf.title_font_size <= 40.0:
        raise ValueError("Invalid texdiff PDF font size")
    if not 0.8 <= theme.pdf.line_height_multiplier <= 3.0 or not 0.0 <= theme.pdf.margin_points <= 144.0:
        raise ValueError("Invalid texdiff PDF layout values")
    return theme


def load_theme(path: str | Path | None = None) -> Theme:
    theme_path = Path(path).resolve() if path else _default_theme_path()
    with theme_path.open("rb") as handle:
        data = tomllib.load(handle)
    try:
        return _validate_theme(Theme(
            colors=Colors(**data["colors"]),
            html=HtmlTheme(**data.get("html", {})),
            pdf=PdfTheme(**data.get("pdf", {})),
        ))
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Invalid texdiff theme file: {theme_path}: {exc}") from exc
