#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-python}"

rm -rf build dist .build-output
mkdir -p .build-output/demo .build-output/benchmark

"$PYTHON" -m compileall -q src tests scripts
if command -v node >/dev/null 2>&1; then node --check src/texdiff/assets/report.js; fi
PYTHONPATH=src "$PYTHON" -m pytest --cov=texdiff --cov-report=term-missing --cov-fail-under=80
"$PYTHON" -m pip wheel . --no-deps --wheel-dir dist
cp dist/texdiff-*.whl .build-output/

PYTHONPATH=src "$PYTHON" -m texdiff \
  examples/old.tex examples/new.tex \
  --extractor builtin \
  --pdf-engine reportlab \
  --html .build-output/demo/sample-diff.html \
  -o .build-output/demo/sample-diff.pdf \
  --json-summary .build-output/demo/sample-summary.json

PYTHONPATH=src "$PYTHON" scripts/benchmark.py --sections 24 --output-dir .build-output/benchmark > .build-output/benchmark-result.json
"$PYTHON" -c "import json; from pathlib import Path; data=json.loads(Path('.build-output/benchmark-result.json').read_text()); assert all(data['blocks'][side][kind] > 0 for side in ('old', 'new') for kind in ('heading', 'paragraph', 'list-item', 'math', 'table')); assert all(item['rows'] >= 3 and item['columns'] == 3 and item['simple'] for side in ('old', 'new') for item in data['tables'][side]); assert all(data['changes'][kind] > 0 for kind in ('modified', 'added', 'deleted', 'moved'))"
"$PYTHON" scripts/check_public_release.py --wheel "dist/$(basename dist/texdiff-*.whl)"

ls -lh dist .build-output .build-output/demo .build-output/benchmark
