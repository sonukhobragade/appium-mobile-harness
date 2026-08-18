.PHONY: help install test lint format check clean dev-setup run-tests

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies using UV
	uv sync

dev-setup: ## Set up development environment
	uv sync
	cp .env.example .env
	uv run pre-commit install

test: ## Run all tests
	uv run pytest

test-unit: ## Run unit tests only
	uv run pytest -m unit -v

test-integration: ## Run integration tests only
	uv run pytest -m integration -v

test-e2e: ## Run end-to-end tests only
	uv run pytest -m e2e -v

test-android: ## Run Android-specific tests
	uv run pytest -m android -v

test-ios: ## Run iOS-specific tests
	uv run pytest -m ios -v

test-smoke: ## Run smoke tests only
	uv run pytest -m smoke -v

test-regression: ## Run regression tests only
	uv run pytest -m regression -v

test-performance: ## Run performance tests only
	uv run pytest -m performance -v

test-parallel: ## Run tests in parallel
	uv run pytest -n auto

test-coverage: ## Run tests with coverage report
	uv run pytest --cov=src --cov-report=html --cov-report=term

lint: ## Run code linting
	uv run ruff check .

format: ## Format code
	uv run ruff format .

type-check: ## Run type checking
	uv run mypy src/

check: lint type-check ## Run all code quality checks

fix: ## Fix linting issues automatically
	uv run ruff check --fix .
	uv run ruff format .

clean: ## Clean up temporary files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf test-results/
	rm -rf allure-results/
	rm -rf allure-report/

allure-generate: ## Generate Allure report
	./allure-cli/bin/allure generate allure-results -o allure-report --clean

allure-serve: ## Serve Allure report (auto-generates if needed)
	./allure-cli/bin/allure serve allure-results

allure-open: ## Open generated Allure report
	./allure-cli/bin/allure open allure-report

