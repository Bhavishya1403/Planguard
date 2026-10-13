.PHONY: install lint format format-check typecheck test test-integration benchmark docs

PYTHON ?= .venv/bin/python
PIP = $(PYTHON) -m pip
RUFF = $(PYTHON) -m ruff
PYTEST = $(PYTHON) -m pytest
MYPY = $(PYTHON) -m mypy

install:
	$(PIP) install -e '.[dev]'

lint:
	$(RUFF) check src tests

format:
	$(RUFF) format src tests

format-check:
	$(RUFF) format --check src tests

typecheck:
	$(MYPY) --strict src

test:
	$(PYTEST) -m 'not integration'

test-integration:
	$(PYTEST) -m integration

benchmark:
	$(PYTHON) benchmarks/run_benchmarks.py

docs:
	$(PYTHON) -m planguard.report.docs
