# Makefile for Land Registry project

.PHONY: help test test-cov test-html test-unit test-integration test-slow clean-cov install lint format check

# Default target
help:
	@echo "Available targets:"
	@echo "  test          - Run all tests"
	@echo "  test-cov      - Run tests with coverage"
	@echo "  test-html     - Run tests with HTML coverage report"
	@echo "  test-unit     - Run only unit tests"
	@echo "  test-integration - Run only integration tests"
	@echo "  test-slow     - Run slow/stress tests"
	@echo "  clean-cov     - Clean coverage files"
	@echo "  install       - Install dependencies"
	@echo "  lint          - Run linting"
	@echo "  format        - Format code"
	@echo "  check         - Run all checks (lint + test)"

# Install dependencies
install:
	uv sync --dev

# Run tests
test:
	uv run pytest tests/ -v

# Run tests with coverage
test-cov:
	uv run pytest tests/ --cov=land_registry --cov-config=.coveragerc --cov-report=term-missing --cov-fail-under=80

# Run tests with HTML coverage report
test-html:
	uv run pytest tests/ --cov=land_registry --cov-config=.coveragerc --cov-report=html:htmlcov --cov-report=term-missing --cov-fail-under=80
	@echo "HTML coverage report generated at: htmlcov/index.html"

# Run unit tests only
test-unit:
	uv run pytest tests/ -m "unit" -v

# Run integration tests only
test-integration:
	uv run pytest tests/ -m "integration" -v

# Run slow tests
test-slow:
	uv run pytest tests/ -m "slow" -v

# Clean coverage files
clean-cov:
	rm -f .coverage
	rm -f coverage.xml
	rm -f coverage.json
	rm -rf htmlcov/
	rm -rf .pytest_cache/

# Linting
lint:
	uv run flake8 land_registry/ tests/

# Code formatting
format:
	uv run black land_registry/ tests/
	uv run isort land_registry/ tests/

# Run all checks
check: lint test-cov

# Quick test (no coverage)
test-quick:
	uv run pytest tests/ --tb=short -q

# Test with parallel execution
test-parallel:
	uv run pytest tests/ -n auto --cov=land_registry --cov-config=.coveragerc

# Generate all coverage reports
test-all-reports:
	uv run pytest tests/ \
		--cov=land_registry \
		--cov-config=.coveragerc \
		--cov-report=html:htmlcov \
		--cov-report=xml:coverage.xml \
		--cov-report=json:coverage.json \
		--cov-report=term-missing \
		--cov-fail-under=80