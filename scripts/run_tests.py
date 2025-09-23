#!/usr/bin/env python3
"""
Test runner script for Land Registry project.

This script provides various options for running tests with coverage reporting.
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path


def run_command(cmd, description=""):
    """Run a command and handle errors."""
    print(f"\n{'='*60}")
    print(f"Running: {description or cmd}")
    print(f"{'='*60}")

    result = subprocess.run(cmd, shell=True, capture_output=False)

    if result.returncode != 0:
        print(f"\n❌ Command failed with exit code {result.returncode}")
        return False
    else:
        print(f"\n✅ Command completed successfully")
        return True


def main():
    parser = argparse.ArgumentParser(description="Run tests for Land Registry project")

    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run tests with coverage reporting"
    )

    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate HTML coverage report"
    )

    parser.add_argument(
        "--xml",
        action="store_true",
        help="Generate XML coverage report"
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Generate JSON coverage report"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Run tests in verbose mode"
    )

    parser.add_argument(
        "--parallel", "-p",
        action="store_true",
        help="Run tests in parallel"
    )

    parser.add_argument(
        "--markers", "-m",
        type=str,
        help="Run tests with specific markers (e.g., 'unit', 'integration', 'slow')"
    )

    parser.add_argument(
        "--test-file", "-f",
        type=str,
        help="Run specific test file"
    )

    parser.add_argument(
        "--test-function", "-k",
        type=str,
        help="Run tests matching pattern"
    )

    parser.add_argument(
        "--fail-fast", "-x",
        action="store_true",
        help="Stop on first test failure"
    )

    parser.add_argument(
        "--no-cov-fail",
        action="store_true",
        help="Don't fail if coverage is below threshold"
    )

    args = parser.parse_args()

    # Change to project root directory
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    # Build pytest command
    cmd_parts = ["uv", "run", "pytest"]

    # Add test path
    if args.test_file:
        cmd_parts.append(f"tests/{args.test_file}")
    else:
        cmd_parts.append("tests/")

    # Add verbose mode
    if args.verbose:
        cmd_parts.append("-v")

    # Add parallel execution
    if args.parallel:
        cmd_parts.extend(["-n", "auto"])

    # Add markers
    if args.markers:
        cmd_parts.extend(["-m", args.markers])

    # Add test pattern
    if args.test_function:
        cmd_parts.extend(["-k", args.test_function])

    # Add fail fast
    if args.fail_fast:
        cmd_parts.append("-x")

    # Add coverage if requested
    if args.coverage:
        cmd_parts.extend([
            "--cov=land_registry",
            "--cov-config=.coveragerc",
            "--cov-report=term-missing"
        ])

        if not args.no_cov_fail:
            cmd_parts.append("--cov-fail-under=80")

        # Add specific coverage reports
        if args.html:
            cmd_parts.append("--cov-report=html:htmlcov")

        if args.xml:
            cmd_parts.append("--cov-report=xml:coverage.xml")

        if args.json:
            cmd_parts.append("--cov-report=json:coverage.json")

    # Run the tests
    cmd = " ".join(cmd_parts)

    print("🧪 Starting Land Registry Test Suite")
    print(f"📁 Working directory: {os.getcwd()}")
    print(f"🔧 Command: {cmd}")

    success = run_command(cmd, "Running tests")

    if success and args.coverage:
        print("\n📊 Coverage Report Generated")

        if args.html:
            html_path = project_root / "htmlcov" / "index.html"
            if html_path.exists():
                print(f"📄 HTML Coverage Report: file://{html_path}")

        if args.xml:
            xml_path = project_root / "coverage.xml"
            if xml_path.exists():
                print(f"📄 XML Coverage Report: {xml_path}")

        if args.json:
            json_path = project_root / "coverage.json"
            if json_path.exists():
                print(f"📄 JSON Coverage Report: {json_path}")

    # Print summary
    if success:
        print(f"\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n💥 Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())