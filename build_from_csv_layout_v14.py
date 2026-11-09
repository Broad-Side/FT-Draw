#!/usr/bin/env python3
"""FT-Draw point renderer V14.

V14 uses V13 as its base and changes shared-common cable labels so the
terminal-side common core label is drawn only once per shared pair. Endpoint
labels remain present at each field device.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

BASE_FILE = Path(__file__).resolve().with_name("build_from_csv_layout_v13.py")


def load_base():
    if not BASE_FILE.exists():
        raise FileNotFoundError(f"Required base renderer not found: {BASE_FILE}")
    spec = importlib.util.spec_from_file_location("ftdraw_layout_v13", BASE_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {BASE_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    renderer = load_base()

    def route_shared_common(
        root,
        prefix,
        source,
        target,
        target_obj,
        row,
        is_mssb,
        registry,
    ):
        source_obj = renderer.find_obj(root, source)
        _, source_y = renderer.centre(source_obj)
        _, target_y = renderer.absolute_centre(target_obj, root)

        terminal = (row.get("common_terminal_id") or "").strip()
        panel = (row.get("common_wire") or "").strip()
        external = (row.get("common_core") or "").strip()

        state = registry.get(target)
        first_shared_member = state is None
        if first_shared_member:
            terminal_id = f"shared_common_terminal_{target}"
            renderer.add_terminal(root, terminal_id, terminal, target_y)
            renderer.add_edge(
                root,
                f"shared_common_panel_{target}",
                terminal_id,
                target,
                renderer.COMMON_EDGE,
            )
            renderer.add_text(
                root,
                f"shared_common_panel_{target}_label",
                panel,
                renderer.PANEL_LABEL_X,
                target_y,
            )
            state = {
                "terminal_id": terminal_id,
                "external": external,
                "target_y": target_y,
            }
            registry[target] = state

        shared_external = state.get("external") or external
        points = None
        if abs(source_y - state["target_y"]) > 0.1:
            points = [
                (renderer.SHARED_COMMON_TRUNK_X, source_y),
                (renderer.SHARED_COMMON_TRUNK_X, state["target_y"]),
            ]

        renderer.add_edge(
            root,
            f"edge_{prefix}_common_external",
            source,
            state["terminal_id"],
            renderer.COMMON_EDGE,
            points,
        )

        # The terminal-side common cable/core label appears once per pair.
        if first_shared_member:
            renderer.add_text(
                root,
                f"edge_{prefix}_common_external_label",
                shared_external,
                renderer.EXTERNAL_LABEL_X,
                source_y,
            )

        # Keep one endpoint-side label beside each field/MSSB device.
        renderer.add_text(
            root,
            f"edge_{prefix}_common_end_label",
            shared_external,
            renderer.MSSB_END_LABEL_X if is_mssb else renderer.FIELD_END_LABEL_X,
            source_y,
        )

    renderer.route_shared_common = route_shared_common
    renderer.main()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
