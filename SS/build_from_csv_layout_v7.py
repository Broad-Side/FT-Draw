#!/usr/bin/env python3
"""Generate left-to-right SNC field wiring from points.csv.

V6 matches supplied expected layout and grouped controller and device Draw.io device templates by flattening their leaf shapes to
page coordinates before placement. It aligns each field signal with the SNC
point row and places the common conductor toward the shared common row.

Run from the FT-Draw repository root:
  python3 build_from_csv_layout_v2.py \
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

# Drawing layout, based on the supplied expected XML.
FIELD_INTERFACE_X = 1180.0
COMMON_VERTICAL_OFFSET = 20.0
COMMON_TRUNK_X_OFFSET = 25.0

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
    parser = argparse.ArgumentParser()
    parser.add_argument("points_csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output.drawio"))
    return parser.parse_args()


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_drawio(path: Path) -> tuple[ET.ElementTree, ET.Element]:
    if not path.exists():
        raise FileNotFoundError(path)
    tree = ET.parse(path)
    graph_root = tree.getroot().find(".//root")
    if graph_root is None:
        raise ValueError(f"No mxGraph root in {path}")
    return tree, graph_root


def find_named_file(root: Path, stem: str, suffix: str) -> Path:
    matches = [p for p in root.rglob(f"*{suffix}") if p.stem.casefold() == stem.casefold()]
    if len(matches) != 1:
        raise ValueError(f"Expected one {stem}{suffix} below {root}, found {matches}")
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


def geometry_of(wrapper: ET.Element) -> ET.Element:
    if wrapper.tag == "object":
        geometry = wrapper.find("./mxCell/mxGeometry")
    else:
        geometry = wrapper.find("mxGeometry")
    if geometry is None:
        raise ValueError(f"No geometry for {wrapper.attrib.get('id')}")
    return geometry


def bounds(wrapper: ET.Element) -> tuple[float, float, float, float]:
    geometry = geometry_of(wrapper)
    return (
        float(geometry.attrib.get("x", 0)),
        float(geometry.attrib.get("y", 0)),
        float(geometry.attrib.get("width", 0)),
        float(geometry.attrib.get("height", 0)),
    )


def absolute_bounds(wrapper: ET.Element, graph_root: ET.Element) -> tuple[float, float, float, float]:
    """Return page-level bounds, including all parent-group offsets."""
    x, y, width, height = bounds(wrapper)
    cell = wrapper.find("mxCell") if wrapper.tag == "object" else wrapper
    parent_id = cell.attrib.get("parent")

    cells = {
        candidate.attrib["id"]: candidate
        for candidate in graph_root.iter("mxCell")
        if candidate.attrib.get("id")
    }

    visited: set[str] = set()
    while parent_id and parent_id not in {"0", "1"} and parent_id not in visited:
        visited.add(parent_id)
        parent_cell = cells.get(parent_id)
        if parent_cell is None:
            break
        parent_geometry = parent_cell.find("mxGeometry")
        if parent_geometry is not None:
            x += float(parent_geometry.attrib.get("x", 0))
            y += float(parent_geometry.attrib.get("y", 0))
        parent_id = parent_cell.attrib.get("parent")

    return x, y, width, height


def absolute_center(wrapper: ET.Element, graph_root: ET.Element) -> tuple[float, float]:
    x, y, width, height = absolute_bounds(wrapper, graph_root)
    return x + width / 2.0, y + height / 2.0


def center(wrapper: ET.Element) -> tuple[float, float]:
    """Local center for flattened page-level device leaves."""
    x, y, width, height = bounds(wrapper)
    return x + width / 2.0, y + height / 2.0


def controller_interfaces(controller_data: dict, point_name: str) -> dict[str, str]:
    try:
        interfaces = controller_data["points"][point_name]["interfaces"]
    except KeyError as exc:
        raise ValueError(f"Missing controller interface mapping for {point_name}: {exc}") from exc
    return {role: data["drawio_id"] for role, data in interfaces.items()}


def device_definition(device_data: dict) -> tuple[dict[str, str], dict[str, str]]:
    template = device_data["device_template"]
    drawing_interfaces = template["drawing"]["interfaces"]
    connections = template["connections"]
    source_ids = {
        role: data.get("drawio_id", data.get("id", role))
        for role, data in drawing_interfaces.items()
    }
    target_roles = {
        role: data["controller_interface"]
        for role, data in connections.items()
    }
    return source_ids, target_roles


def source_cells(graph_root: ET.Element) -> dict[str, ET.Element]:
    """Index all source mxCells that have IDs."""
    result: dict[str, ET.Element] = {}
    for cell in graph_root.iter("mxCell"):
        cell_id = cell.attrib.get("id")
        if cell_id:
            result[cell_id] = cell
    return result


def absolute_xy(cell: ET.Element, cells: dict[str, ET.Element]) -> tuple[float, float]:
    geometry = cell.find("mxGeometry")
    x = float(geometry.attrib.get("x", 0)) if geometry is not None else 0.0
    y = float(geometry.attrib.get("y", 0)) if geometry is not None else 0.0
    parent_id = cell.attrib.get("parent")
    visited: set[str] = set()
    while parent_id and parent_id not in {"0", "1"} and parent_id not in visited:
        visited.add(parent_id)
        parent_cell = cells.get(parent_id)
        if parent_cell is None:
            break
        parent_geometry = parent_cell.find("mxGeometry")
        if parent_geometry is not None:
            x += float(parent_geometry.attrib.get("x", 0))
            y += float(parent_geometry.attrib.get("y", 0))
        parent_id = parent_cell.attrib.get("parent")
    return x, y


def is_group(cell: ET.Element) -> bool:
    style = cell.attrib.get("style", "")
    return "group" in style and cell.attrib.get("connectable") == "0"


def flatten_device_template(
    device_root: ET.Element,
    prefix: str,
) -> tuple[list[ET.Element], dict[str, str]]:
    """Copy leaf objects/cells as page-level elements with absolute coordinates.

    This avoids broken parent references when the source template uses groups.
    """
    cells = source_cells(device_root)
    output: list[ET.Element] = []
    id_map: dict[str, str] = {}

    for child in list(device_root):
        if child.tag not in {"object", "mxCell"}:
            continue

        if child.tag == "object":
            cell = child.find("mxCell")
            source_id = child.attrib.get("id")
        else:
            cell = child
            source_id = child.attrib.get("id")

        if cell is None or source_id in {None, "0", "1"}:
            continue
        if is_group(cell):
            continue
        if cell.attrib.get("vertex") != "1":
            continue

        clone = copy.deepcopy(child)
        clone_cell = clone.find("mxCell") if clone.tag == "object" else clone
        clone_geometry = clone_cell.find("mxGeometry")
        if clone_geometry is None:
            continue

        abs_x, abs_y = absolute_xy(cell, cells)
        clone_geometry.attrib["x"] = str(abs_x)
        clone_geometry.attrib["y"] = str(abs_y)
        clone_cell.attrib["parent"] = "1"

        new_id = f"{prefix}_{source_id}"
        clone.attrib["id"] = new_id
        id_map[source_id] = new_id
        output.append(clone)

    return output, id_map


def move_leaf(leaf: ET.Element, dx: float, dy: float) -> None:
    geometry = geometry_of(leaf)
    geometry.attrib["x"] = str(float(geometry.attrib.get("x", 0)) + dx)
    geometry.attrib["y"] = str(float(geometry.attrib.get("y", 0)) + dy)


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
            "value": "",
            "style": style,
        },
    )
    geometry = ET.SubElement(edge, "mxGeometry", {"relative": "1", "as": "geometry"})
    if waypoints:
        geometry.attrib["x"] = "-0.039"
        array = ET.SubElement(geometry, "Array", {"as": "points"})
        for x, y in waypoints:
            ET.SubElement(array, "mxPoint", {"x": str(round(x, 3)), "y": str(round(y, 3))})
    graph_root.append(edge)


def label_device(
    leaves: list[ET.Element],
    destination_ref: str,
    point_tag: str,
    description: str,
) -> None:
    """Replace the main device-body label without relying on template name.

    The widest non-interface vertex is treated as the visible device body.
    This works for AI_10K and AI_10K_Invert even when both Draw.io templates
    still contain the visible text AI_10K.
    """
    candidates: list[tuple[float, ET.Element]] = []

    for leaf in leaves:
        cell = leaf.find("mxCell") if leaf.tag == "object" else leaf
        if cell is None or cell.attrib.get("vertex") != "1":
            continue

        geometry = cell.find("mxGeometry")
        if geometry is None:
            continue

        width = float(geometry.attrib.get("width", 0))
        if width <= 40:
            # Ignore the small + and - interface objects.
            continue

        candidates.append((width, cell))

    if not candidates:
        raise ValueError(f"{point_tag}: no device body was found")

    device_body = max(candidates, key=lambda item: item[0])[1]

    # point_tag is the primary visible identifier. Fall back to destination_ref.
    visible_label = point_tag or destination_ref
    device_body.attrib["value"] = (
        '<span style="font-family: &quot;Courier New&quot;;">'
        f"{visible_label}</span>"
    )



def align_device_body_to_interfaces(
    leaves: list[ET.Element],
    signal_obj: ET.Element,
    common_obj: ET.Element,
) -> None:
    """Align the field-device body with the upper + or - interface row."""
    body_top = min(bounds(signal_obj)[1], bounds(common_obj)[1])
    candidates: list[tuple[float, ET.Element]] = []

    for leaf in leaves:
        cell = leaf.find("mxCell") if leaf.tag == "object" else leaf
        if cell is None or cell.attrib.get("vertex") != "1":
            continue
        geometry = cell.find("mxGeometry")
        if geometry is None:
            continue
        width = float(geometry.attrib.get("width", 0))
        if width > 40:
            candidates.append((width, geometry))

    if not candidates:
        return

    body_geometry = max(candidates, key=lambda item: item[0])[1]
    body_geometry.attrib["y"] = str(body_top)

def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"No rows in {path}")
    return rows



FIELD_INTERFACE_X = 1001.0
MSSB_INTERFACE_X = 756.0
TERMINAL_X = 520.0
TERMINAL_WIDTH = 60.0
TERMINAL_HEIGHT = 12.0
MSSB_LEFT_X = 680.0
MSSB_RIGHT_X = 918.0


def csv_value(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def add_edge_label(
    graph_root: ET.Element,
    edge_id: str,
    label: str,
    label_id: str,
    position: float = 0.0,
    offset_x: float = 0.0,
) -> None:
    if not label:
        return
    cell = ET.Element(
        "mxCell",
        {
            "id": label_id,
            "connectable": "0",
            "parent": edge_id,
            "style": (
                "edgeLabel;html=1;align=center;verticalAlign=middle;"
                "resizable=0;points=[];inheritText=1;"
            ),
            "value": label,
            "vertex": "1",
        },
    )
    geometry = ET.SubElement(
        cell,
        "mxGeometry",
        {"relative": "1", "x": str(position), "as": "geometry"},
    )
    ET.SubElement(geometry, "mxPoint", {"x": str(offset_x), "as": "offset"})
    graph_root.append(cell)


def add_terminal(
    graph_root: ET.Element,
    terminal_id: str,
    visible_text: str,
    center_y: float,
) -> str:
    cell = ET.Element(
        "mxCell",
        {
            "id": terminal_id,
            "parent": "1",
            "style": "rounded=0;whiteSpace=wrap;html=1;fontSize=10;",
            "value": visible_text,
            "vertex": "1",
        },
    )
    ET.SubElement(
        cell,
        "mxGeometry",
        {
            "height": str(TERMINAL_HEIGHT),
            "width": str(TERMINAL_WIDTH),
            "x": str(TERMINAL_X),
            "y": str(center_y - TERMINAL_HEIGHT / 2.0),
            "as": "geometry",
        },
    )
    graph_root.append(cell)
    return terminal_id


def add_boundary_line(graph_root: ET.Element, line_id: str, x: float) -> None:
    edge = ET.Element(
        "mxCell",
        {
            "id": line_id,
            "edge": "1",
            "parent": "1",
            "style": "endArrow=none;dashed=1;html=1;rounded=0;defaultLabelBackgroundColor=page;",
            "value": "",
        },
    )
    geometry = ET.SubElement(
        edge,
        "mxGeometry",
        {"height": "50", "relative": "1", "width": "50", "as": "geometry"},
    )
    ET.SubElement(geometry, "mxPoint", {"x": str(x), "y": "925", "as": "sourcePoint"})
    ET.SubElement(geometry, "mxPoint", {"x": str(x), "y": "25", "as": "targetPoint"})
    graph_root.append(edge)


def add_routed_edge(
    graph_root: ET.Element,
    edge_id: str,
    source_id: str,
    target_id: str,
    style: str,
    label: str = "",
    label_position: float = 0.0,
    waypoints: list[tuple[float, float]] | None = None,
) -> None:
    add_edge(graph_root, edge_id, source_id, target_id, style, waypoints)
    add_edge_label(
        graph_root,
        edge_id=edge_id,
        label=label,
        label_id=f"{edge_id}_label",
        position=label_position,
    )


def point_is_mssb(row: dict[str, str]) -> bool:
    return (
        csv_value(row, "destination_type").lower() == "mssb"
        or csv_value(row, "signal_terminal_mode").lower() == "mssb"
        or csv_value(row, "common_terminal_mode").lower() == "mssb"
    )


def route_conductor(
    graph_root: ET.Element,
    prefix: str,
    role: str,
    device_interface_id: str,
    controller_target_id: str,
    controller_target: ET.Element,
    device_interface: ET.Element,
    row: dict[str, str],
) -> None:
    mode = csv_value(row, f"{role}_terminal_mode").lower()
    terminal_text = csv_value(row, f"{role}_terminal_id")
    panel_label = csv_value(row, f"{role}_wire")
    external_label = csv_value(row, f"{role}_core")

    _, device_y = center(device_interface)
    controller_x, controller_y = absolute_center(controller_target, graph_root)

    use_terminal = mode in {"terminal", "mssb"} and bool(terminal_text)
    common_waypoints = None
    if role == "common":
        trunk_x = controller_x + COMMON_TRUNK_X_OFFSET
        common_waypoints = [(trunk_x, device_y), (trunk_x, controller_y)]

    if not use_terminal:
        add_routed_edge(
            graph_root,
            edge_id=f"edge_{prefix}_{role}",
            source_id=device_interface_id,
            target_id=controller_target_id,
            style=COMMON_EDGE_STYLE if role == "common" else SIGNAL_EDGE_STYLE,
            label=panel_label or external_label,
            waypoints=common_waypoints,
        )
        return

    terminal_object_id = add_terminal(
        graph_root,
        terminal_id=f"terminal_{prefix}_{role}",
        visible_text=terminal_text,
        center_y=device_y,
    )

    # Device or MSSB side of terminal.
    add_routed_edge(
        graph_root,
        edge_id=f"edge_{prefix}_{role}_external",
        source_id=device_interface_id,
        target_id=terminal_object_id,
        style=SIGNAL_EDGE_STYLE,
        label=external_label,
        label_position=0.60,
    )

    # Controller side of terminal. The common keeps its small orthogonal rise/drop.
    panel_waypoints = None
    if role == "common":
        trunk_x = controller_x + COMMON_TRUNK_X_OFFSET
        panel_waypoints = [(trunk_x, device_y), (trunk_x, controller_y)]

    add_routed_edge(
        graph_root,
        edge_id=f"edge_{prefix}_{role}_panel",
        source_id=terminal_object_id,
        target_id=controller_target_id,
        style=COMMON_EDGE_STYLE if role == "common" else SIGNAL_EDGE_STYLE,
        label=panel_label,
        label_position=-0.35,
        waypoints=panel_waypoints,
    )


def main() -> int:
    args = parse_args()
    controller_data = load_yaml(CONTROLLER_YAML)
    output_tree, output_root = load_drawio(CONTROLLER_DRAWIO)
    rows = read_rows(args.points_csv)

    built = 0
    mssb_boundaries_added = False

    for row_number, row in enumerate(rows, start=1):
        tag = csv_value(row, "point_tag") or f"POINT-{row_number}"
        point_name = csv_value(row, "controller_point")
        template_name = csv_value(row, "device_template")
        if not point_name or not template_name:
            print(f"SKIP row {row_number}: missing controller_point or device_template")
            continue

        point_interfaces = controller_interfaces(controller_data, point_name)
        signal_target_id = point_interfaces["signal"]
        configured_common_id = point_interfaces["common"]
        signal_target = find_object(output_root, signal_target_id)
        configured_common = find_object(output_root, configured_common_id)
        common_target = maybe_find_object(output_root, f"z{configured_common_id}") or configured_common
        common_target_id = common_target.attrib["id"]

        device_yaml_path = find_named_file(DEVICE_YAML_ROOT, template_name, ".yml")
        device_drawio_path = find_named_file(DEVICE_DRAWIO_ROOT, template_name, ".drawio")
        device_data = load_yaml(device_yaml_path)
        _, device_root = load_drawio(device_drawio_path)

        point_type = controller_data["points"][point_name]["point_type"]
        compatible = device_data["device_template"].get("compatible_point_types", [])
        if compatible and point_type not in compatible:
            raise ValueError(f"{tag}: {template_name} is incompatible with {point_type}")

        source_ids, _ = device_definition(device_data)
        prefix = f"d{row_number}_{uuid.uuid4().hex[:8]}"
        leaves, id_map = flatten_device_template(device_root, prefix)
        source_signal_id = id_map[source_ids["signal"]]
        source_common_id = id_map[source_ids["common"]]

        temp_root = ET.Element("root")
        for leaf in leaves:
            temp_root.append(leaf)
        signal_leaf = find_object(temp_root, source_signal_id)
        common_leaf = find_object(temp_root, source_common_id)

        is_mssb = point_is_mssb(row)
        endpoint_x = MSSB_INTERFACE_X if is_mssb else FIELD_INTERFACE_X
        if is_mssb and not mssb_boundaries_added:
            add_boundary_line(output_root, "mssb_boundary_left", MSSB_LEFT_X)
            add_boundary_line(output_root, "mssb_boundary_right", MSSB_RIGHT_X)
            mssb_boundaries_added = True

        _, target_signal_y = absolute_center(signal_target, output_root)
        source_signal_x, source_signal_y = center(signal_leaf)
        dx = endpoint_x + bounds(signal_leaf)[2] / 2.0 - source_signal_x
        dy = target_signal_y - source_signal_y
        for leaf in leaves:
            move_leaf(leaf, dx, dy)

        signal_leaf = find_object(temp_root, source_signal_id)
        common_leaf = find_object(temp_root, source_common_id)
        _, signal_y = center(signal_leaf)
        _, common_target_y = absolute_center(common_target, output_root)
        desired_common_y = signal_y + (
            COMMON_VERTICAL_OFFSET if signal_y < common_target_y else -COMMON_VERTICAL_OFFSET
        )
        common_geometry = geometry_of(common_leaf)
        common_height = float(common_geometry.attrib.get("height", 0))
        common_geometry.attrib["y"] = str(desired_common_y - common_height / 2.0)
        align_device_body_to_interfaces(leaves, signal_leaf, common_leaf)

        label_device(
            leaves=leaves,
            destination_ref=csv_value(row, "destination_ref"),
            point_tag=tag,
            description=csv_value(row, "description"),
        )
        for leaf in leaves:
            output_root.append(leaf)

        signal_leaf = find_object(output_root, source_signal_id)
        common_leaf = find_object(output_root, source_common_id)

        route_conductor(
            output_root,
            prefix,
            "signal",
            source_signal_id,
            signal_target_id,
            signal_target,
            signal_leaf,
            row,
        )
        route_conductor(
            output_root,
            prefix,
            "common",
            source_common_id,
            common_target_id,
            common_target,
            common_leaf,
            row,
        )

        built += 1
        print(
            f"BUILT {tag}: point={point_name}, endpoint={'mssb' if is_mssb else 'field'}, "
            f"signal_terminal={csv_value(row, 'signal_terminal_id') or '-'}, "
            f"common_terminal={csv_value(row, 'common_terminal_id') or '-'}"
        )

    if built == 0:
        raise ValueError("No points were built")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(output_tree, space="  ")
    output_tree.write(args.output, encoding="utf-8", xml_declaration=True)
    print(f"Created {args.output}: built {built}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
