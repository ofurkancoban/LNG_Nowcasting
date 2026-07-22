.PHONY: check lint typecheck test

check: lint typecheck test

lint:
	uv run ruff check src tests

typecheck:
	uv run mypy src

test:
	uv run pytest tests/
