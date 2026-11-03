#!/usr/bin/env python3
"""Build a simple controller-to-field Draw.io diagram from points.csv.

Version 1 scope:
- One SNC25152 controller graphic
- Field destinations only
- Direct signal/common conductors
- No terminal-strip objects yet

Example:
  python3 build_from_csv.py \
    project/MSFT/SYD06/COLO5/CE1/points.csv \
    --output output.drawio
"""

from __future__ import annotations

import argparse
import copy
import csv
from pathlib import Path
import sys
import uuid
import xml.etree.ElementTree as ET

import yaml


REPO_ROOT = Path(__file__).resolve().parent
CONTROLLER_YAML = REPO_ROOT / "controllerTemplates" / "SNC25152.yml"
CONTROLLER_DRAWIO = REPO_ROOT / "drawioTemplates" / "controllers" / "SNC25152.drawio"
DEVICE_YAML_ROOT = REPO_ROOT / "deviceTemplates"
DEVICE_DRAWIO_ROOT = REPO_ROOT / "drawioTemplates" / "devices"

ROW_SPACING = 140.0
DEVICE_X_SHIFT = 650.0
DEVICE_Y_START = 140.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a simple Draw.io wiring diagram from points.csv")
    parser.add_argument("points_csv", type=Path, help="Path to the project points.csv")
    parser.add_argument("--output", type=Path, default=Path("output.drawio"), help="Output Draw.io file")
    return parser.parse_args()


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"YAML file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_drawio(path: Path) -> tuple[ET.ElementTree[ET.Element], ET.Element]:
    if not path.exists():
        raise FileNotFoundError(f"Draw.io template not found: {path}")
    tree = ET.parse(path)
    graph_root = tree.getroot().find(".//root")
    if graph_root is None:
        raise ValueError(f"No mxGraph root found in {path}")
    return tree, graph_root


def find_named_file(root: Path, stem: str, suffix: str) -> Path:
    matches = [p for p in root.rglob(f"*{suffix}") if p.stem.casefold() == stem.casefold()]
    if not matches:
        raise FileNotFoundError(f"Could not find {stem}{suffix} below {root}")
    if len(matches) > 1:
        raise ValueError(f"More than one {stem}{suffix} found: {matches}")
    return matches[0]


def find_object(graph_root: ET.Element, object_id: str) -> ET.Element:
    for obj in graph_root.iter("object"):
        if obj.attrib.get("id") == object_id:
            return obj
    raise ValueError(f"Draw.io object '{object_id}' was not found")


def object_geometry(obj: ET.Element) -> ET.Element:
    geometry = obj.find("./mxCell/mxGeometry")
    if geometry is None:
        raise ValueError(f"Object '{obj.attrib.get('id')}' has no mxGeometry")
    return geometry


def set_vertex_label(graph_root: ET.Element, old_value: str, new_value: str) -> None:
    for cell in graph_root.iter("mxCell"):
        if cell.attrib.get("value") == old_value:
            cell.attrib["value"] = new_value
            return


def remap_ids(element: ET.Element, prefix: str) -> dict[str, str]:
    """Make every copied object/cell ID unique and repair internal references."""
    mapping: dict[str, str] = {}
    for child in element.iter():
        old_id = child.attrib.get("id")
        if old_id:
            mapping[old_id] = f"{prefix}_{old_id}"

    for child in element.iter():
        if child.attrib.get("id") in mapping:
            child.attrib["id"] = mapping[child.attrib["id"]]
        for attr in ("parent", "source", "target"):
            value = child.attrib.get(attr)
            if value in mapping:
                child.attrib[attr] = mapping[value]
    return mapping


def shift_top_level_geometry(element: ET.Element, dx: float, dy: float) -> None:
    """Shift only geometry whose owning cell is parented to page root 1."""
    for cell in element.iter("mxCell"):
        if cell.attrib.get("parent") != "1":
            continue
        geometry = cell.find("mxGeometry")
        if geometry is None or geometry.attrib.get("relative") == "1":
            continue
        geometry.attrib["x"] = str(float(geometry.attrib.get("x", 0)) + dx)
        geometry.attrib["y"] = str(float(geometry.attrib.get("y", 0)) + dy)


def controller_interfaces(controller_data: dict, point_name: str) -> dict[str, str]:
    points = controller_data.get("points", {})
    if point_name not in points:
        raise ValueError(f"Controller point '{point_name}' is not defined in SNC25152.yml")

    point = points[point_name]
    interfaces = point.get("interfaces")
    if interfaces:
        return {
            name: details["drawio_id"]
            for name, details in interfaces.items()
        }

    # Temporary backward compatibility with the original SNC YAML.
    result = {"signal": point_name}
    if "common_terminal" in point:
        result["common"] = f"{point_name}_COMMON"
    return result


def device_connections(device_data: dict) -> tuple[dict[str, str], dict[str, str]]:
    template = device_data.get("device_template", {})
    drawing_interfaces = template.get("drawing", {}).get("interfaces", {})
    connections = template.get("connections", {})

    source_ids = {
        name: details.get("drawio_id", details.get("id", name))
        for name, details in drawing_interfaces.items()
    }
    targets = {
        name: details["controller_interface"]
        for name, details in connections.items()
    }
    return source_ids, targets


def add_edge(graph_root: ET.Element, edge_id: str, source: str, target: str, label: str) -> None:
    edge = ET.Element(
        "mxCell",
        {
            "id": edge_id,
            "edge": "1",
            "parent": "1",
            "source": source,
            "target": target,
            "value": label,
            "style": "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=none;strokeWidth=2;",
        },
    )
    ET.SubElement(edge, "mxGeometry", {"relative": "1", "as": "geometry"})
    graph_root.append(edge)


def read_points(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"No point rows found in {path}")
    return rows


def main() -> int:
    args = parse_args()
    controller_data = load_yaml(CONTROLLER_YAML)
    output_tree, output_root = load_drawio(CONTROLLER_DRAWIO)
    rows = read_points(args.points_csv)

    built = 0
    skipped = 0

    for index, row in enumerate(rows, start=1):
        tag = (row.get("point_tag") or f"POINT-{index}").strip()
        destination_type = (row.get("destination_type") or "field").strip().lower()

        if destination_type != "field":
            print(f"SKIP {tag}: destination_type '{destination_type}' needs an MSSB template, which is not implemented in version 1")
            skipped += 1
            continue

        device_template = (row.get("device_template") or "").strip()
        controller_point = (row.get("controller_point") or "").strip()
        if not device_template or not controller_point:
            raise ValueError(f"{tag}: device_template and controller_point are required")

        device_yaml_path = find_named_file(DEVICE_YAML_ROOT, device_template, ".yml")
        device_drawio_path = find_named_file(DEVICE_DRAWIO_ROOT, device_template, ".drawio")
        device_data = load_yaml(device_yaml_path)
        _, device_root = load_drawio(device_drawio_path)

        point_type = controller_data["points"][controller_point]["point_type"]
        compatible = device_data.get("device_template", {}).get("compatible_point_types", [])
        if compatible and point_type not in compatible:
            raise ValueError(f"{tag}: {device_template} cannot connect to point type {point_type}")

        target_interfaces = controller_interfaces(controller_data, controller_point)
        source_interfaces, connections = device_connections(device_data)

        prefix = f"d{index}_{uuid.uuid4().hex[:8]}"
        copied_children: list[ET.Element] = []
        id_mapping: dict[str, str] = {}

        for child in list(device_root):
            if child.attrib.get("id") in {"0", "1"}:
                continue
            clone = copy.deepcopy(child)
            child_map = remap_ids(clone, prefix)
            id_mapping.update(child_map)
            shift_top_level_geometry(clone, DEVICE_X_SHIFT, DEVICE_Y_START + (index - 1) * ROW_SPACING)
            copied_children.append(clone)

        for clone in copied_children:
            output_root.append(clone)

        # Replace the visible template name with the project point tag.
        set_vertex_label(output_root, device_template, f"{tag}<br>{row.get('description', '').strip()}")

        for role, controller_interface in connections.items():
            source_template_id = source_interfaces.get(role)
            target_template_id = target_interfaces.get(controller_interface)
            if not source_template_id or not target_template_id:
                raise ValueError(
                    f"{tag}: cannot map role '{role}' to controller interface '{controller_interface}'"
                )

            source_id = id_mapping.get(source_template_id)
            if not source_id:
                raise ValueError(f"{tag}: interface '{source_template_id}' not found in {device_drawio_path}")

            find_object(output_root, target_template_id)
            label = "SIG" if role == "signal" else "COM" if role == "common" else role.upper()
            add_edge(output_root, f"edge_{prefix}_{role}", source_id, target_template_id, label)

        built += 1
        print(f"BUILT {tag}: {device_template} -> {controller_point}")

    if built == 0:
        raise ValueError("No field points were built. Add a field row or create an MSSB endpoint template.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(output_tree, space="  ")
    output_tree.write(args.output, encoding="utf-8", xml_declaration=True)
    print(f"Created {args.output} with {built} point(s); skipped {skipped}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
