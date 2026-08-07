# Architecture

## Pipeline

```text
local files or Git revisions
        |
        v
source loading and bounded child-file expansion
        |
        v
built-in extractor
  headings / prose / lists / math / tables
        |
        +-- optional explicit Pandoc AST extractor
        v
block alignment
        |
        v
move pairing and token/cell-level diff
        |
        +-- self-contained interactive HTML
        +-- native ReportLab PDF
```

## Source Loading

`source.py` reads local UTF-8 files and expands `\\input` and `\\include` only inside the main source directory. `git_source.py` reads blobs with `git show` and applies the same boundary to each revision without checking it out.

## Extraction

The built-in extractor preserves structures needed by the report:

- headings become `HEADING` blocks;
- prose, list items, and quotes become text blocks;
- display formulas become `MATH` blocks;
- inline formulas remain embedded as normalized source;
- simple tables become `TableData` grids;
- figures and image contents are removed.

Pandoc remains available through `--extractor pandoc` for inputs where its AST provides a better structural view.

## Alignment

The comparison pipeline normalizes block keys, selects stable exact anchors, aligns gaps with bounded dynamic programming, and uses RapidFuzz for candidate similarity. Move candidates are indexed by block kind and tokens instead of scanning every deleted and added pair.

## Structured Differences

`TextBlock` can carry a `TableData` payload. `table_diff.py` aligns table rows, compares cells by position, and emits shared status information for HTML and PDF.

Formula blocks are compared as normalized source. The renderer never evaluates arbitrary TeX.

## HTML

`render.py` reads the bundled theme, CSS, and JavaScript assets. CSS and JavaScript are embedded into the generated file. There are no remote resources. Browser annotations modify the DOM locally, and the save action serializes the updated DOM into another single HTML file.

## PDF

`native_pdf.py` draws the report directly with ReportLab. It supports page splitting for long rows, table grids, cached text measurements, and CJK CID font selection. Browser and WeasyPrint backends remain optional alternatives in `pdf.py`.
