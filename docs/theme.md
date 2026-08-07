# Theme Configuration

The developer-facing theme is `src/texdiff/assets/theme.toml`. It is packaged with the project and loaded through `importlib.resources`.

## Colors

Color values use CSS-style hexadecimal notation and are shared by HTML and PDF output.

```toml
[colors]
page_background = "#ffffff"
panel = "#f6f8fa"
border = "#c8d1dc"
text = "#17202a"
muted = "#66717e"
added_background = "#eefaf1"
added_strong = "#ccefd4"
deleted_background = "#fff2f1"
deleted_strong = "#ffd8d5"
modified_background = "#edf3f8"
moved_background = "#eef6ff"
search_highlight = "#ffe8a3"
annotation_highlight = "#dcecff"
focus = "#2563eb"
```

Background colors fill rows or cells. Strong colors mark changed tokens inside those rows.

## HTML

```toml
[html]
font_size_px = 14
line_height = 1.55
sticky_header = true
default_view = "context"
```

`default_view` accepts `context`, `changes`, `all`, or `unchanged`.

## PDF

```toml
[pdf]
page_size = "A4"
orientation = "landscape"
body_font_size = 7.7
heading_font_size = 8.8
title_font_size = 9.6
line_height_multiplier = 1.38
margin_points = 24.0
```

The native backend supports A4 portrait and landscape output.

## Temporary Override

Pass another TOML file without changing the packaged theme:

```bash
texdiff old.tex new.tex --theme experiments/dark.toml -o changes.pdf
```
