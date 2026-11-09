#!/usr/bin/env python3
"""Generate controller-to-field Draw.io wiring from points.csv.

This version follows the supplied expected layout:
- controller graphic remains on the left
- field devices are placed on the right
- each signal interface aligns with its controller point row
- common interfaces are placed toward the shared common row
- common conductors route through a short vertical trunk
- terminal strips and MSSB endpoints are intentionally deferred

Run from the FT-Draw repository root:
  python3 build_from_csv_layout.py \
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

# Layout values taken from the supplied expected Draw.io XML.
FIELD_INTERFACE_X = 1180.0
COMMON_OFFSET_Y = 20.0
COMMON_TRUNK_OFFSET_X = 35.0

SIGNAL_EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;"
    "jettySize=auto;html=1;endArrow=none;strokeWidth=2;jumpStyle=arc;"
)
COMMON_EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;"
    "jettySize=auto;html=1;endArrow=none;strokeWidth=2;"
    "entryX=1;entryY=0.5;entryDx=0;entryDy=0;jumpStyle=arc;"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Draw.io controller wiring from points.csv")
    parser.add_argument("points_csv", type=Path, help="Project points.csv")
    parser.add_argument("--output", type=Path, default=Path("output.drawio"))
    return parser.parse_args()


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"YAML file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_drawio(path: Path) -> tuple[ET.ElementTree, ET.Element]:
    if not path.exists():
        raise FileNotFoundError(f"Draw.io file not found: {path}")
    tree = ET.parse(path)
    graph_root = tree.getroot().find(".//root")
    if graph_root is None:
        raise ValueError(f"No mxGraph root in {path}")
    return tree, graph_root


def find_named_file(root: Path, stem: str, suffix: str) -> Path:
    matches = [p for p in root.rglob(f"*{suffix}") if p.stem.casefold() == stem.casefold()]
    if not matches:
        raise FileNotFoundError(f"Could not find {stem}{suffix} below {root}")
    if len(matches) > 1:
        raise ValueError(f"Multiple {stem}{suffix} files found: {matches}")
    return matches[0]


def find_object(graph_root: ET.Element, object_id: str) -> ET.Element:
    for obj in graph_root.iter("object"):
        if obj.attrib.get("id") == object_id:
            return obj
    raise ValueError(f"Draw.io object '{object_id}' not found")


def maybe_find_object(graph_root: ET.Element, object_id: str) -> ET.Element | None:
    try:
        return find_object(graph_root, object_id)
    except ValueError:
        return None


def geometry_of(obj: ET.Element) -> ET.Element:
    geometry = obj.find("./mxCell/mxGeometry")
    if geometry is None:
        raise ValueError(f"Object '{obj.attrib.get('id')}' has no geometry")
    return geometry


def bounds(obj: ET.Element) -> tuple[float, float, float, float]:
    geometry = geometry_of(obj)
    return (
        float(geometry.attrib.get("x", 0)),
        float(geometry.attrib.get("y", 0)),
        float(geometry.attrib.get("width", 0)),
        float(geometry.attrib.get("height", 0)),
    )


def center(obj: ET.Element) -> tuple[float, float]:
    x, y, width, height = bounds(obj)
    return x + width / 2.0, y + height / 2.0


def remap_ids(element: ET.Element, prefix: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for node in element.iter():
        old_id = node.attrib.get("id")
        if old_id:
            mapping[old_id] = f"{prefix}_{old_id}"

    for node in element.iter():
        old_id = node.attrib.get("id")
        if old_id in mapping:
            node.attrib["id"] = mapping[old_id]
        for attribute in ("parent", "source", "target"):
            value = node.attrib.get(attribute)
            if value in mapping:
                node.attrib[attribute] = mapping[value]
    return mapping


def shift_top_level(element: ET.Element, dx: float, dy: float) -> None:
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
        raise ValueError(f"Controller point '{point_name}' is missing from SNC25152.yml")

    point = points[point_name]
    interfaces = point.get("interfaces", {})
    if not interfaces:
        raise ValueError(f"Controller point '{point_name}' has no interfaces mapping")

    return {
        role: details["drawio_id"]
        for role, details in interfaces.items()
    }


def device_definition(device_data: dict) -> tuple[dict[str, str], dict[str, str]]:
    template = device_data.get("device_template", {})
    interfaces = template.get("drawing", {}).get("interfaces", {})
    connections = template.get("connections", {})

    source_ids = {
        role: details.get("drawio_id", details.get("id", role))
        for role, details in interfaces.items()
    }
    target_roles = {
        role: details["controller_interface"]
        for role, details in connections.items()
    }
    return source_ids, target_roles


def add_edge(
    graph_root: ET.Element,
    edge_id: str,
    source_id: str,
    target_id: str,
    style: str,
    waypoints: list[tuple[float, float]] | None = None,
) -> None:
    edge = ET.Element(
        "mxCell",
        {
            "id": edge_id,
            "edge": "1",
            "parent": "1",
            "source": source_id,
            "target": target_id,
            "style": style,
            "value": "",
        },
    )
    geometry_attributes = {"relative": "1", "as": "geometry"}
    if waypoints:
        geometry_attributes["x"] = "-0.039"
    geometry = ET.SubElement(edge, "mxGeometry", geometry_attributes)
    if waypoints:
        points_array = ET.SubElement(geometry, "Array", {"as": "points"})
        for x, y in waypoints:
            ET.SubElement(points_array, "mxPoint", {"x": str(round(x, 3)), "y": str(round(y, 3))})
    graph_root.append(edge)


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"No rows found in {path}")
    return rows


def label_device(clones: list[ET.Element], template_name: str, label: str) -> None:
    for clone in clones:
        for cell in clone.iter("mxCell"):
            value = cell.attrib.get("value", "")
            if template_name in value:
                # Keep the expected output style while using the CSV destination/tag.
                cell.attrib["value"] = (
                    '<span style="font-family: &quot;Courier New&quot;;">'
                    f"{label}</span>"
                )
                return


def main() -> int:
    args = parse_args()
    controller_data = load_yaml(CONTROLLER_YAML)
    output_tree, output_root = load_drawio(CONTROLLER_DRAWIO)
    rows = read_rows(args.points_csv)

    built = 0
    skipped = 0

    for row_number, row in enumerate(rows, start=1):
        tag = (row.get("point_tag") or f"POINT-{row_number}").strip()
        destination_type = (row.get("destination_type") or "field").strip().lower()
        if destination_type != "field":
            print(f"SKIP {tag}: '{destination_type}' endpoint is not implemented yet")
            skipped += 1
            continue

        point_name = (row.get("controller_point") or "").strip()
        template_name = (row.get("device_template") or "").strip()
        if not point_name or not template_name:
            raise ValueError(f"{tag}: controller_point and device_template are required")

        point_interfaces = controller_interfaces(controller_data, point_name)
        signal_target_id = point_interfaces["signal"]
        configured_common_id = point_interfaces["common"]

        signal_target = find_object(output_root, signal_target_id)
        configured_common = find_object(output_root, configured_common_id)

        # The expected controller graphic uses a visible common bus object prefixed with z.
        common_bus_id = f"z{configured_common_id}"
        common_target = maybe_find_object(output_root, common_bus_id) or configured_common
        common_target_id = common_target.attrib["id"]

        device_yaml_path = find_named_file(DEVICE_YAML_ROOT, template_name, ".yml")
        device_drawio_path = find_named_file(DEVICE_DRAWIO_ROOT, template_name, ".drawio")
        device_data = load_yaml(device_yaml_path)
        _, device_root = load_drawio(device_drawio_path)

        point_type = controller_data["points"][point_name]["point_type"]
        compatible = device_data.get("device_template", {}).get("compatible_point_types", [])
        if compatible and point_type not in compatible:
            raise ValueError(f"{tag}: {template_name} is not compatible with {point_type}")

        source_ids, connection_roles = device_definition(device_data)
        if "signal" not in source_ids or "common" not in source_ids:
            raise ValueError(f"{template_name}: signal and common interfaces are required")

        prefix = f"d{row_number}_{uuid.uuid4().hex[:8]}"
        clones: list[ET.Element] = []
        id_map: dict[str, str] = {}

        for child in list(device_root):
            if child.attrib.get("id") in {"0", "1"}:
                continue
            clone = copy.deepcopy(child)
            id_map.update(remap_ids(clone, prefix))
            clones.append(clone)

        signal_source_id = id_map[source_ids["signal"]]
        common_source_id = id_map[source_ids["common"]]

        # Locate interfaces inside the copied fragments before adding them to the page.
        temp_root = ET.Element("root")
        for clone in clones:
            temp_root.append(clone)
        signal_source = find_object(temp_root, signal_source_id)
        common_source = find_object(temp_root, common_source_id)

        signal_target_x, signal_target_y = center(signal_target)
        common_target_x, common_target_y = center(common_target)
        source_x, source_y = center(signal_source)

        # Align signal to its controller row and put the device at the expected field X.
        dx = FIELD_INTERFACE_X - bounds(signal_source)[0]
        dy = signal_target_y - source_y
        for clone in clones:
            shift_top_level(clone, dx, dy)

        # Place common toward the shared common row, matching the supplied XML layout.
        signal_source = find_object(temp_root, signal_source_id)
        common_source = find_object(temp_root, common_source_id)
        _, shifted_signal_y = center(signal_source)
        desired_common_center_y = shifted_signal_y + (
            COMMON_OFFSET_Y if shifted_signal_y < common_target_y else -COMMON_OFFSET_Y
        )
        common_geometry = geometry_of(common_source)
        common_height = float(common_geometry.attrib.get("height", 0))
        common_geometry.attrib["y"] = str(desired_common_center_y - common_height / 2.0)

        device_label = (row.get("destination_ref") or template_name).strip()
        label_device(clones, template_name, device_label)

        for clone in clones:
            output_root.append(clone)

        # Add signal conductor.
        add_edge(
            output_root,
            f"edge_{prefix}_signal",
            signal_source_id,
            signal_target_id,
            SIGNAL_EDGE_STYLE,
        )

        # Add common conductor with the same vertical-trunk structure as the expected XML.
        common_source = find_object(output_root, common_source_id)
        _, common_source_y = center(common_source)
        trunk_x = common_target_x + COMMON_TRUNK_OFFSET_X
        add_edge(
            output_root,
            f"edge_{prefix}_common",
            common_source_id,
            common_target_id,
            COMMON_EDGE_STYLE,
            [(trunk_x, common_source_y), (trunk_x, common_target_y)],
        )

        built += 1
        print(
            f"BUILT {tag}: {template_name} -> {point_name} "
            f"(common -> {common_target_id})"
        )

    if built == 0:
        raise ValueError("No field rows were built")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(output_tree, space="  ")
    output_tree.write(args.output, encoding="utf-8", xml_declaration=True)
    print(f"Created {args.output} with {built} field point(s); skipped {skipped}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
