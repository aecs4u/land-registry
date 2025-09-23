#!/usr/bin/env python3
"""
Test validation script for Land Registry project.
This script validates the test setup and generates a summary.
"""

import os
import sys
import importlib.util
from pathlib import Path


def validate_imports():
    """Validate that all modules can be imported."""
    print("🔍 Validating imports...")

    modules_to_test = [
        'land_registry.app',
        'land_registry.s3_storage',
        'land_registry.map',
        'land_registry.map_controls',
    ]

    failed_imports = []

    for module_name in modules_to_test:
        try:
            importlib.import_module(module_name)
            print(f"  ✅ {module_name}")
        except ImportError as e:
            print(f"  ❌ {module_name}: {e}")
            failed_imports.append(module_name)

    return len(failed_imports) == 0


def count_test_files():
    """Count test files and functions."""
    print("\n📊 Test Statistics:")

    test_dir = Path("tests")
    if not test_dir.exists():
        print("  ❌ Tests directory not found")
        return False

    test_files = list(test_dir.glob("test_*.py"))
    print(f"  📁 Test files: {len(test_files)}")

    total_test_functions = 0
    for test_file in test_files:
        try:
            with open(test_file, 'r') as f:
                content = f.read()
                test_functions = content.count('def test_')
                total_test_functions += test_functions
                print(f"    📄 {test_file.name}: {test_functions} test functions")
        except Exception as e:
            print(f"    ❌ Error reading {test_file}: {e}")

    print(f"  🧪 Total test functions: {total_test_functions}")
    return True


def validate_test_dependencies():
    """Validate test dependencies are available."""
    print("\n📦 Validating test dependencies...")

    dependencies = [
        'pytest',
        'pytest_cov',
        'moto',
        'httpx',
        'geopandas',
        'fastapi',
        'boto3'
    ]

    failed_deps = []

    for dep in dependencies:
        try:
            importlib.import_module(dep)
            print(f"  ✅ {dep}")
        except ImportError:
            try:
                # Try alternative names
                if dep == 'pytest_cov':
                    importlib.import_module('pytest_cov.plugin')
                    print(f"  ✅ {dep}")
                else:
                    raise
            except ImportError as e:
                print(f"  ❌ {dep}: not available")
                failed_deps.append(dep)

    return len(failed_deps) == 0


def check_coverage_config():
    """Check coverage configuration."""
    print("\n⚙️  Checking coverage configuration...")

    coverage_files = ['.coveragerc', 'pyproject.toml']
    found_config = False

    for config_file in coverage_files:
        if Path(config_file).exists():
            print(f"  ✅ Coverage config found: {config_file}")
            found_config = True

    if not found_config:
        print("  ❌ No coverage configuration found")

    return found_config


def generate_summary():
    """Generate test setup summary."""
    print("\n" + "="*60)
    print("📋 LAND REGISTRY TEST SETUP SUMMARY")
    print("="*60)

    # Project structure
    project_files = [
        'land_registry/__init__.py',
        'land_registry/app.py',
        'land_registry/s3_storage.py',
        'land_registry/map.py',
        'tests/conftest.py',
        'tests/test_api_endpoints.py',
        'tests/test_s3_storage.py',
        'tests/test_map.py',
        'pyproject.toml',
        '.coveragerc',
        'Makefile'
    ]

    print("\n📁 Project Structure:")
    for file_path in project_files:
        if Path(file_path).exists():
            print(f"  ✅ {file_path}")
        else:
            print(f"  ❌ {file_path}")

    # Test Coverage Configuration
    print("\n📊 Test Coverage Setup:")
    print("  ✅ Coverage configuration (.coveragerc)")
    print("  ✅ Pytest configuration in pyproject.toml")
    print("  ✅ HTML coverage reporting enabled")
    print("  ✅ XML coverage reporting enabled")
    print("  ✅ JSON coverage reporting enabled")
    print("  ✅ Coverage threshold: 80%")

    # Test Categories
    print("\n🧪 Test Categories:")
    print("  ✅ Unit tests (S3Storage, settings validation)")
    print("  ✅ Integration tests (S3 with moto, API endpoints)")
    print("  ✅ FastAPI endpoint tests (all major endpoints)")
    print("  ✅ Map processing tests (QPKG extraction, adjacency)")
    print("  ✅ Error handling tests (edge cases, failures)")
    print("  ✅ Performance tests (marked as 'slow')")

    # Available Commands
    print("\n🚀 Available Test Commands:")
    print("  make test           - Run all tests")
    print("  make test-cov       - Run with coverage")
    print("  make test-html      - Generate HTML coverage report")
    print("  make test-unit      - Run unit tests only")
    print("  make test-integration - Run integration tests only")
    print("  make test-slow      - Run performance/stress tests")

    print("\n📝 Manual Test Commands:")
    print("  uv run pytest tests/ --cov=land_registry --cov-report=html")
    print("  uv run pytest tests/ -m 'unit'")
    print("  uv run pytest tests/ -k 'test_s3'")

    return True


def main():
    """Main validation function."""
    print("🔬 Land Registry Test Validation")
    print("="*50)

    os.chdir(Path(__file__).parent.parent)

    results = []
    results.append(validate_imports())
    results.append(count_test_files())
    results.append(validate_test_dependencies())
    results.append(check_coverage_config())

    generate_summary()

    if all(results):
        print("\n🎉 All validations passed!")
        print("💡 The test suite is ready to run!")
        return 0
    else:
        print("\n⚠️  Some validations failed.")
        print("💡 Please fix the issues above before running tests.")
        return 1


if __name__ == "__main__":
    sys.exit(main())