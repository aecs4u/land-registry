"""Runs the parcel panel's Node unit tests (tests/js) as part of the suite."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_parcel_panel_node_unit_tests_pass():
    result = subprocess.run(
        ["node", "--test", str(ROOT / "tests" / "js" / "parcel_panel.test.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
