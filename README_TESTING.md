# Land Registry Testing Guide

Comprehensive testing documentation for the Land Registry application.

## 🧪 Test Suite Overview

### Test Statistics
- **Total Test Files**: 5
- **Total Test Functions**: 110
- **Coverage Target**: 80%
- **Test Categories**: Unit, Integration, Performance

### Test Files Structure
```
tests/
├── conftest.py              # Test fixtures and configuration
├── test_api_endpoints.py    # FastAPI endpoint tests (32 tests)
├── test_map.py             # Map processing tests (20 tests)
├── test_map_additional.py  # Edge cases and error handling (17 tests)
├── test_map_controls.py    # Map controls tests (19 tests)
└── test_s3_storage.py      # S3 storage functionality (22 tests)
```

## 🚀 Quick Test Commands

### Basic Testing
```bash
# Run all tests
make test

# Run with coverage
make test-cov

# Generate HTML coverage report
make test-html

# Quick test (no coverage)
make test-quick
```

### Targeted Testing
```bash
# Unit tests only
make test-unit

# Integration tests only
make test-integration

# Performance/stress tests
make test-slow

# Parallel execution
make test-parallel
```

### Manual Commands
```bash
# Run specific test file
uv run pytest tests/test_s3_storage.py -v

# Run tests matching pattern
uv run pytest tests/ -k "test_s3" -v

# Run with markers
uv run pytest tests/ -m "unit" -v

# Run with detailed output
uv run pytest tests/ -v --tb=long
```

## 📊 Coverage Configuration

### Coverage Settings
- **Source**: `land_registry/` package
- **Target**: 80% minimum coverage
- **Branch Coverage**: Enabled
- **Report Formats**: HTML, XML, JSON, Terminal

### Coverage Reports
```bash
# Generate all report formats
make test-all-reports

# View HTML report
open htmlcov/index.html

# Check coverage data
coverage report --show-missing
```

### Excluded from Coverage
- Test files (`*/tests/*`, `*/test_*`)
- Cache directories (`*/__pycache__/*`)
- Virtual environments (`*/venv/*`, `*/.venv/*`)
- Abstract methods and protocols
- Debug code blocks
- Type checking imports

## 🎯 Test Categories

### 1. Unit Tests (`@pytest.mark.unit`)

**S3Storage Tests**
- Settings validation and configuration
- Basic S3 operations (file existence, listing)
- Error handling for invalid credentials
- Mock-based testing without external dependencies

**Map Processing Tests**
- QPKG/GPKG data extraction
- GeoJSON parsing and validation
- Geometry operations
- Feature ID assignment

**Configuration Tests**
- Pydantic model validation
- Environment variable handling
- Default value assignment

### 2. Integration Tests (`@pytest.mark.integration`)

**API Endpoint Tests**
- FastAPI application testing with TestClient
- Request/response validation
- Authentication and authorization
- Error response handling
- File upload processing

**S3 Integration Tests**
- Moto-based S3 mocking
- Full S3 workflow testing
- Fallback mechanisms (S3 → local files)
- Multi-file operations

**Database Integration**
- Global state management
- Data persistence across requests
- Transaction handling

### 3. Performance Tests (`@pytest.mark.slow`)

**Stress Tests**
- Large dataset processing
- Multiple file operations
- Concurrent request handling
- Memory usage validation

**Load Tests**
- High-volume polygon processing
- Complex geometry operations
- Large ZIP file extraction

## 🔧 Test Infrastructure

### Fixtures (`conftest.py`)

**Application Fixtures**
```python
@pytest.fixture
def client():
    """FastAPI test client"""

@pytest.fixture
def sample_gdf():
    """Sample GeoDataFrame for testing"""

@pytest.fixture
def sample_geojson():
    """Sample GeoJSON data"""
```

**S3 Testing Fixtures**
```python
@pytest.fixture
def mock_s3_client():
    """Mocked S3 client using moto"""

@pytest.fixture
def s3_storage_with_data():
    """S3Storage instance with test data"""

@pytest.fixture
def s3_settings():
    """S3 configuration for testing"""
```

**File System Fixtures**
```python
@pytest.fixture
def temp_qpkg_file():
    """Temporary QPKG file for testing"""

@pytest.fixture
def temp_cadastral_data_file():
    """Temporary cadastral structure file"""
```

### Mocking Strategy

**S3 Mocking with Moto**
```python
from moto import mock_aws

@pytest.fixture
def mock_s3_client():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-bucket")
        yield client
```

**FastAPI Testing**
```python
from fastapi.testclient import TestClient

def test_endpoint():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
```

## 🧩 Test Examples

### Unit Test Example
```python
def test_s3_settings_validation():
    """Test S3 settings validation."""
    settings = S3Settings(
        s3_bucket_name="test-bucket",
        s3_region="us-east-1"
    )
    assert settings.s3_bucket_name == "test-bucket"
    assert settings.s3_region == "us-east-1"
```

### Integration Test Example
```python
def test_load_cadastral_files_from_s3_success(mock_get_storage, client):
    """Test loading cadastral files from S3."""
    mock_storage = MagicMock()
    mock_storage.read_multiple_files.return_value = [test_data]
    mock_get_storage.return_value = mock_storage

    response = client.post("/load-cadastral-files/", json={"files": ["test.shp"]})

    assert response.status_code == 200
    assert response.json()["source"] == "S3"
```

### Performance Test Example
```python
@pytest.mark.slow
def test_find_adjacent_polygons_large_dataset():
    """Test adjacency finding with large dataset."""
    # Create 100 polygons in a grid
    polygons = [create_polygon(i, j) for i in range(10) for j in range(10)]
    gdf = gpd.GeoDataFrame(geometry=polygons)

    result = find_adjacent_polygons(gdf, 55, "touches")
    assert isinstance(result, list)
```

## 🔍 Error Handling Tests

### Edge Cases Covered
- **Invalid File Formats**: Non-geospatial files, corrupted data
- **Network Issues**: S3 connection failures, timeouts
- **Geometry Errors**: Invalid polygons, self-intersections
- **Memory Constraints**: Large file processing limits
- **Permission Issues**: Access denied, authentication failures

### Error Test Example
```python
def test_extract_qpkg_data_corrupted_zip():
    """Test QPKG extraction with corrupted ZIP file."""
    with tempfile.NamedTemporaryFile(suffix='.qpkg') as temp_file:
        temp_file.write(b'PK\x03\x04corrupted_data')
        temp_file.flush()

        result = extract_qpkg_data(temp_file.name)
        assert result is None  # Should handle gracefully
```

## 🎛️ Test Configuration

### pytest.ini Settings (in pyproject.toml)
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "--strict-markers",
    "--verbose",
    "--cov=land_registry",
    "--cov-report=html:htmlcov",
    "--cov-report=term-missing",
    "--cov-fail-under=80"
]
markers = [
    "unit: marks tests as unit tests",
    "integration: marks tests as integration tests",
    "slow: marks tests as slow",
    "parser: marks tests for parser functionality",
    "database: marks tests for database operations",
    "web: marks tests for web interface"
]
```

### Coverage Configuration (.coveragerc)
```ini
[run]
source = land_registry
branch = True
omit =
    */tests/*
    */test_*
    */__pycache__/*

[report]
fail_under = 80
show_missing = True
exclude_lines =
    pragma: no cover
    def __repr__
    raise NotImplementedError
    if __name__ == .__main__.:
```

## 🚀 Continuous Integration

### Pre-commit Hooks
```bash
# Install pre-commit hooks
pre-commit install

# Run manually
pre-commit run --all-files
```

### CI Pipeline Example
```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    - name: Setup Python
      uses: actions/setup-python@v2
      with:
        python-version: '3.11'
    - name: Install dependencies
      run: uv sync --dev
    - name: Run tests
      run: make test-cov
    - name: Upload coverage
      uses: codecov/codecov-action@v1
```

## 🐛 Debugging Tests

### Debug Failed Tests
```bash
# Run with pdb on failures
uv run pytest tests/ --pdb

# Show local variables on failure
uv run pytest tests/ --tb=long -v

# Stop on first failure
uv run pytest tests/ -x

# Run last failed tests only
uv run pytest tests/ --lf
```

### Test Output Control
```bash
# Quiet mode
uv run pytest tests/ -q

# Verbose mode with capture disabled
uv run pytest tests/ -v -s

# Show print statements
uv run pytest tests/ --capture=no
```

## 📝 Writing New Tests

### Test File Template
```python
"""
Tests for [module_name] functionality.
"""

import pytest
from unittest.mock import patch, MagicMock

from land_registry.[module_name] import [function_to_test]


class Test[ClassName]:
    """Test [class/module] functionality."""

    def test_[function_name]_success(self):
        """Test successful [operation]."""
        # Arrange
        test_input = "test_data"

        # Act
        result = function_to_test(test_input)

        # Assert
        assert result is not None
        assert result.property == expected_value

    def test_[function_name]_error_handling(self):
        """Test error handling for [operation]."""
        with pytest.raises(ExpectedException):
            function_to_test(invalid_input)

    @pytest.mark.integration
    def test_[function_name]_integration(self, mock_dependency):
        """Test integration with external systems."""
        # Integration test implementation
        pass

    @pytest.mark.slow
    def test_[function_name]_performance(self):
        """Test performance with large datasets."""
        # Performance test implementation
        pass
```

### Best Practices
1. **One concept per test** - Test one specific behavior
2. **Clear test names** - Describe what is being tested
3. **Arrange-Act-Assert** - Structure tests clearly
4. **Use fixtures** - Reuse test data and setup
5. **Mock external dependencies** - Keep tests isolated
6. **Test edge cases** - Include error conditions
7. **Meaningful assertions** - Check specific outcomes

## 📊 Test Metrics

### Current Coverage Targets
- **Overall Coverage**: 80%
- **Unit Tests Coverage**: 90%
- **Integration Tests Coverage**: 75%
- **Critical Path Coverage**: 95%

### Quality Metrics
- **Test Execution Time**: < 2 minutes
- **Test Reliability**: > 99% pass rate
- **Flaky Test Rate**: < 1%
- **Code Duplication in Tests**: < 5%