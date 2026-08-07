PYTHON ?= python
.PHONY: install install-dev check test coverage benchmark demo build clean

install:
	$(PYTHON) -m pip install -e .

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

check:
	$(PYTHON) -m compileall -q src tests scripts
	@if command -v node >/dev/null 2>&1; then node --check src/texdiff/assets/report.js; fi
	PYTHONPATH=src $(PYTHON) -m pytest
	PYTHONPATH=src $(PYTHON) -m pytest --cov=texdiff --cov-report=term-missing --cov-fail-under=80

test:
	PYTHONPATH=src $(PYTHON) -m pytest

coverage:
	PYTHONPATH=src $(PYTHON) -m pytest --cov=texdiff --cov-report=html --cov-report=term-missing

benchmark:
	PYTHONPATH=src $(PYTHON) scripts/benchmark.py

demo:
	mkdir -p .demo-output
	PYTHONPATH=src $(PYTHON) -m texdiff examples/old.tex examples/new.tex --extractor builtin --html .demo-output/sample-diff.html -o .demo-output/sample-diff.pdf --json-summary .demo-output/sample-summary.json

build:
	bash scripts/build_release.sh

clean:
	rm -rf build dist src/*.egg-info .pytest_cache .coverage htmlcov .benchmark .benchmark-check .demo-output .build-output .playwright-cli
	find src tests scripts -type d -name __pycache__ -prune -exec rm -rf {} +
