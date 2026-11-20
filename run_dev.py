#!/usr/bin/env python3
"""Compatibility entry point for the development start profile."""

from __future__ import annotations

import os
from pathlib import Path
import sys


if __name__ == "__main__":
    start_script = Path(__file__).resolve().parent / "scripts" / "start.sh"
    os.execv(
        str(start_script),
        [str(start_script), "--host", "127.0.0.1", "--port", "8000", *sys.argv[1:]],
    )
