#!/usr/bin/env python3
from __future__ import annotations

import runpy
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "runtime" / "docsctl.py"
if __name__ == "__main__":
    sys.argv = [str(SCRIPT), "rollback", *sys.argv[1:]]
    runpy.run_path(str(SCRIPT), run_name="__main__")
