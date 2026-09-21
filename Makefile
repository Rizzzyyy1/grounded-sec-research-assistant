.DEFAULT_GOAL := help
PY ?= python3.13
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

$(VENV):
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip

.PHONY: install
install: $(VENV)  ## Create venv and install everything needed to run `make check`
	$(BIN)/pip install -e ".[dev,data,ml,llm,api,ui]"
	$(BIN)/pre-commit install

.PHONY: install-all
install-all: $(VENV)  ## Install every extra, including eval and notebook tooling
	$(BIN)/pip install -e ".[dev,all]"

.PHONY: lint
lint:  ## Ruff lint + format check
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

.PHONY: format
format:  ## Auto-fix lint issues and format
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

.PHONY: typecheck
typecheck:  ## mypy --strict
	$(BIN)/mypy

.PHONY: arch
arch:  ## Architecture contracts (import-linter, see docs/ARCHITECTURE.md section 3)
	$(BIN)/lint-imports

.PHONY: test
test:  ## Fast hermetic tests (unit)
	$(BIN)/pytest -m "not network and not llm and not slow"

.PHONY: test-all
test-all:  ## Everything, including network / LLM tests (costs money)
	$(BIN)/pytest

.PHONY: cov
cov:  ## Tests with coverage report
	$(BIN)/pytest --cov --cov-report=term-missing:skip-covered

.PHONY: check
check: lint typecheck arch test  ## What CI runs: lint + types + architecture + tests

.PHONY: doctor
doctor:  ## Check environment, credentials and installed extras
	$(BIN)/finsight doctor

.PHONY: clean
clean:  ## Remove caches and build artefacts (keeps data/)
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
