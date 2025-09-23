#!/usr/bin/env python3
"""
Generate requirements.txt files from pyproject.toml dependencies.

This script reads the pyproject.toml file and generates:
- requirements.txt (main dependencies)
- requirements-dev.txt (development dependencies)
- requirements-test.txt (test dependencies)
"""

import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    # Python < 3.11
    try:
        import tomli as tomllib
    except ImportError:
        print("ERROR: tomli package required for Python < 3.11")
        print("Install with: pip install tomli")
        sys.exit(1)


def load_pyproject_toml(path: Path) -> dict:
    """Load pyproject.toml file."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def write_requirements_file(dependencies: list, output_path: Path) -> None:
    """Write dependencies to requirements file."""
    with open(output_path, "w") as f:
        f.write("# This file is auto-generated from pyproject.toml\n")
        f.write("# To update, run: uv run scripts/generate_requirements.py\n\n")
        for dep in sorted(dependencies):
            f.write(f"{dep}\n")
    print(f"Generated: {output_path}")


def main():
    """Generate requirements files from pyproject.toml."""
    project_root = Path(__file__).parent.parent
    pyproject_path = project_root / "pyproject.toml"
    
    if not pyproject_path.exists():
        print(f"ERROR: {pyproject_path} not found")
        sys.exit(1)
    
    # Load pyproject.toml
    data = load_pyproject_toml(pyproject_path)
    project = data.get("project", {})
    
    # Main dependencies
    dependencies = project.get("dependencies", [])
    requirements_path = project_root / "requirements.txt"
    write_requirements_file(dependencies, requirements_path)
    
    # Optional dependencies
    optional_deps = project.get("optional-dependencies", {})
    
    # Development dependencies
    if "dev" in optional_deps:
        dev_deps = dependencies + optional_deps["dev"]
        dev_requirements_path = project_root / "requirements-dev.txt"
        write_requirements_file(dev_deps, dev_requirements_path)
    
    # Test dependencies
    if "test" in optional_deps:
        test_deps = dependencies + optional_deps["test"]
        test_requirements_path = project_root / "requirements-test.txt"
        write_requirements_file(test_deps, test_requirements_path)
    
    print("\nRequirements files generated successfully!")
    print("You can now install dependencies with:")
    print("  uv pip install -e .                 # Main dependencies")
    print("  uv pip install -e .[dev]            # Development dependencies")  
    print("  uv pip install -e .[test]           # Test dependencies")
    print("  uv pip install -r requirements.txt  # Main dependencies (alternative)")


if __name__ == "__main__":
    main()