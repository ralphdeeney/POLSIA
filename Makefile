.PHONY: install dev lint typecheck layering test migrate seed clean

install:
	pip install -e ".[dev]"

dev:
	uvicorn polsia.api.main:app --reload --port 8000

lint:
	ruff check polsia tests scripts
	ruff format --check polsia tests scripts

format:
	ruff format polsia tests scripts
	ruff check --fix polsia tests scripts

typecheck:
	mypy polsia

layering:
	python scripts/check_layering.py

test:
	python -m pytest

migrate:
	alembic upgrade head

seed:
	python -m polsia.infra.seed

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
