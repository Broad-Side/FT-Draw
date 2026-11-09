#!/usr/bin/env python3
"""FT-Draw project builder V21 using the V14 point renderer."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE_FILE = HERE / "build_project_drawings_v20.py"
POINT_RENDERER = HERE / "build_from_csv_layout_v14.py"


def main() -> None:
    if not BASE_FILE.exists():
        raise FileNotFoundError(f"Required base project builder not found: {BASE_FILE}")
    if not POINT_RENDERER.exists():
        raise FileNotFoundError(f"Required point renderer not found: {POINT_RENDERER}")
    spec = importlib.util.spec_from_file_location("ftdraw_project_v20", BASE_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {BASE_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.POINT_RENDERER = POINT_RENDERER
    module.main()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
