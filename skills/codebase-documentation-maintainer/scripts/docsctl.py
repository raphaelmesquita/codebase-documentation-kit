#!/usr/bin/env python3
"""Small launcher for the toolkit runtime. It contains no documentation policy."""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def candidates() -> list[Path]:
    here = Path(__file__).resolve()
    result: list[Path] = []
    env = os.environ.get("CODEBASE_DOC_KIT_RUNTIME")
    if env:
        result.append(Path(env).expanduser() / "docsctl.py")
    for parent in here.parents:
        result.extend([
            parent / ".codebase-documentation-kit" / "runtime" / "docsctl.py",
            parent / "runtime" / "docsctl.py",
        ])
    result.append(Path.home() / ".codebase-documentation-kit" / "runtime" / "docsctl.py")
    return result


def main() -> None:
    for script in candidates():
        if script.is_file() and script.resolve() != Path(__file__).resolve():
            sys.argv = [str(script), *sys.argv[1:]]
            runpy.run_path(str(script), run_name="__main__")
            return
    raise SystemExit("Codebase Documentation Kit runtime not found. Re-run install.py for this environment.")


if __name__ == "__main__":
    main()
