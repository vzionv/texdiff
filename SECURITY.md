# Security

`texdiff` reads LaTeX as text.

- Local `\input` and `\include` expansion is restricted to the top-level source directory.
- Git revision expansion is restricted to repository-relative paths and reads blobs with `git show`.
- Subprocesses use argument arrays and never use `shell=True`.
- Generated HTML escapes document text and contains no remote scripts, fonts, stylesheets, or network calls.
- Browser annotations remain local and are written only when a user explicitly saves an annotated copy.

Do not process confidential documents with third-party online converters. Report path traversal, HTML escaping, command execution, or unsafe annotation serialization issues privately before public disclosure.
