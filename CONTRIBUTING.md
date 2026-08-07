# Contributing

## Development Setup

Use Python 3.10 or newer:

```bash
python -m pip install -e ".[dev]"
make check
```

Every behavior change should include focused tests. Run the structured benchmark when changing block alignment, extraction, or report generation:

```bash
make benchmark
```

## Implementation Guidelines

- Keep extraction conservative. Preserving source or using a visible fallback is better than executing untrusted TeX.
- Keep generated HTML self-contained and free of remote resources.
- Use `src/texdiff/assets/theme.toml` for shared visual values.
- Keep public documentation and code comments in English.
- Do not commit generated PDFs, HTML reports, wheels, caches, or local environments.

## Pull Requests

Describe the behavior changed and include the commands used for verification. Add regression coverage for parsing, alignment, rendering, or command-line behavior when applicable.
