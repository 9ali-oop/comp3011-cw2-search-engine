# Convenience targets for the search-engine project.
# Run with: make <target>

PYTHON ?= python

.PHONY: help install test cov lint typecheck check build load verify clean

help:
	@echo "Targets:"
	@echo "  install     pip install requirements (incl. dev tools)"
	@echo "  test        run the unit + integration tests"
	@echo "  cov         run tests with coverage report"
	@echo "  lint        run ruff in lint-only mode"
	@echo "  typecheck   run mypy"
	@echo "  check       lint + typecheck + tests with coverage gate"
	@echo "  build       crawl the live site and persist data/index.json"
	@echo "  load        report on a previously built index"
	@echo "  verify      run verify_index.py against data/index.json"
	@echo "  clean       remove generated artefacts (pycache, coverage, etc.)"

install:
	$(PYTHON) -m pip install -r requirements.txt
	$(PYTHON) -m pip install ruff mypy

test:
	$(PYTHON) -m pytest tests/ -q

cov:
	$(PYTHON) -m pytest tests/ --cov=src --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check src/ tests/

typecheck:
	$(PYTHON) -m mypy src/

check: lint typecheck
	$(PYTHON) -m pytest tests/ --cov=src --cov-fail-under=90 -q

build:
	$(PYTHON) -X utf8 -m src.main

verify:
	$(PYTHON) -X utf8 verify_index.py
	$(PYTHON) -X utf8 post_build_report.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf .pytest_cache .coverage htmlcov coverage.xml .mypy_cache .ruff_cache
