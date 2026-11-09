#!/usr/bin/env python3
"""Build multi-page FT-Draw drawings with background, title block and revisions.

This test wrapper uses build_from_csv_layout_v12.py for the controller wiring,
then places each controller page onto backgroundTemplates/Background.drawio.

Expected project folder:
  project/testPanel/
    points.csv
    panelDetails.yml
    revHistory.csv
    controllers.yml          # optional

Usage:
  python3 build_project_drawings_v13.py project/testPanel --output output.drawio
  python3 build_project_drawings_v13.py project/testPanel --no-revision-prompt
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import date, datetime
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import yaml

ROOT = Path(__file__).resolve().parent
POINT_RENDERER = ROOT / "build_from_csv_layout_v12.py"
DEFAULT_BACKGROUND = ROOT / "backgroundTemplates" / "Background.drawio"

STATIC_IDS = (
    "Project", "Client", "Vendor", "Drafter_Company", "Drawing_Set",
    "Designer", "Drafter", "Checked",
)


def cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build FT-Draw project pages")
    p.add_argument("project_dir", type=Path)
    p.add_argument("--output", type=Path, default=Path("output.drawio"))
    p.add_argument("--background", type=Path, default=DEFAULT_BACKGROUND)
    p.add_argument("--no-revision-prompt", action="store_true")
    p.add_argument("--section-name", default="UI Connections")
    return p.parse_args()


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def project_values(data: dict) -> dict[str, str]:
    # Supports either flat YAML or a top-level Project mapping.
    values = data.get("Project", data)
    return {str(k): "" if v is None else str(v) for k, v in values.items()}


def read_csv(path: Path) -> list[dict[str, str]]:
    """Read CSV while normalising whitespace around header names and values."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        rows: list[dict[str, str]] = []
        for raw_row in reader:
            clean_row: dict[str, str] = {}
            for raw_key, raw_value in raw_row.items():
                if raw_key is None:
                    continue
                key = raw_key.strip()
                value = raw_value.strip() if isinstance(raw_value, str) else (raw_value or "")
                clean_row[key] = value
            rows.append(clean_row)
        return rows


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    """Write only the declared canonical fields, tolerating legacy extra keys."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})



def format_revision_date(value: str) -> str:
    """Return revision dates as yy-mm-dd, accepting common legacy formats."""
    value = (value or "").strip()
    if not value:
        return ""
    for pattern in ("%Y-%m-%d", "%y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, pattern).strftime("%y-%m-%d")
        except ValueError:
            pass
    return value

def latest_revision(rows: list[dict[str, str]]) -> dict[str, str]:
    valid = [r for r in rows if (r.get("Rev") or "").strip()]
    if not valid:
        return {"Rev": "", "Date": "", "Description": "", "Drafter": "", "Reviewer": ""}
    try:
        return max(valid, key=lambda r: int((r.get("Rev") or "0").strip()))
    except ValueError:
        return valid[-1]


def next_revision(current: str) -> str:
    try:
        return str(int(current) + 1)
    except (TypeError, ValueError):
        return ""


def prompt_revision(path: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    current = latest_revision(rows)
    print(f"Current revision: {current.get('Rev') or 'none'}")
    answer = input("Create a new revision? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        return rows

    suggested = next_revision(current.get("Rev", ""))
    rev = input(f"Revision number [{suggested}]: ").strip() or suggested
    rev_date = input(f"Revision date [{date.today().strftime("%y-%m-%d")}]: ").strip() or date.today().strftime("%y-%m-%d")
    description = input("Revision description: ").strip()
    drafter = input("Drafter initials: ").strip()
    reviewer = input("Reviewer initials: ").strip()
    if not rev or not description:
        raise ValueError("Revision number and description are required")

    rev_date = format_revision_date(rev_date)
    rows.append({
        "Rev": rev, "Date": rev_date, "Description": description,
        "Drafter": drafter, "Reviewer": reviewer,
    })
    write_csv(path, rows, ["Rev", "Date", "Description", "Drafter", "Reviewer"])
    return rows



def title_block_value(object_id: str, value: str) -> str:
    """Apply required title-block typography to major title fields."""
    if object_id in {"Project", "Client", "Vendor"}:
        return f'<b><font style="font-size: 24px;">{value}</font></b>'
    return value

def set_value(root: ET.Element, object_id: str, value: str) -> bool:
    """Set a named object; for group IDs update the primary visible child."""
    value = title_block_value(object_id, value)
    for obj in root.iter("object"):
        if obj.get("id") != object_id:
            continue
        obj.set("label", value)
        cell = obj.find("mxCell")
        if cell is not None and "group" not in cell.get("style", ""):
            cell.set("value", value)
            return True
        # Group object: update the largest non-caption child vertex.
        candidates = []
        for child in root.iter("mxCell"):
            if child.get("parent") != object_id or child.get("vertex") != "1":
                continue
            geo = child.find("mxGeometry")
            if geo is None:
                continue
            h = float(geo.get("height", 0)); w = float(geo.get("width", 0))
            candidates.append((h * w, h, child))
        if candidates:
            # Prefer the largest/tallest field rather than the 10px caption.
            max(candidates, key=lambda x: (x[0], x[1]))[2].set("value", value)
        return True

    for cell in root.iter("mxCell"):
        if cell.get("id") == object_id:
            cell.set("value", value)
            return True
    return False


def set_latest_revision(root: ET.Element, rev: dict[str, str]) -> None:
    set_value(root, "Rev_Number", rev.get("Rev", ""))
    set_value(root, "Rev_Details", rev.get("Description", ""))

    # Preferred IDs for the visible revision row.
    mapping = {
        "Rev_Row_1_Rev": rev.get("Rev", ""),
        "Rev_Row_1_Date": format_revision_date(rev.get("Date", "")),
        "Rev_Row_1_Description": rev.get("Description", ""),
        "Rev_Row_1_Checked": rev.get("Reviewer", ""),
    }
    hits = sum(set_value(root, key, value) for key, value in mapping.items())
    if hits:
        return

    # Fallback for the sample background: top row at page y=797.
    cells = []
    for cell in root.iter("mxCell"):
        geo = cell.find("mxGeometry")
        if geo is None or cell.get("vertex") != "1":
            continue
        y = float(geo.get("y", -999)); x = float(geo.get("x", 0))
        if abs(y - 797) < 1:
            cells.append((x, cell))
    cells.sort(key=lambda item: item[0])
    values = [
        rev.get("Rev", ""),
        format_revision_date(rev.get("Date", "")),
        rev.get("Description", ""),
        rev.get("Reviewer", ""),
    ]
    for (_, cell), value in zip(cells, values):
        cell.set("value", value)


def load_diagram(path: Path) -> tuple[ET.ElementTree, ET.Element, ET.Element]:
    tree = ET.parse(path)
    diagram = tree.getroot().find("diagram")
    graph = tree.getroot().find(".//mxGraphModel")
    root = tree.getroot().find(".//root")
    if diagram is None or graph is None or root is None:
        raise ValueError(f"Invalid Draw.io file: {path}")
    return tree, graph, root


def build_body(points_file: Path, output: Path) -> None:
    command = [sys.executable, str(POINT_RENDERER), str(points_file), "--output", str(output)]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    if result.stdout:
        print(result.stdout.rstrip())


def merge_body(background_root: ET.Element, body_root: ET.Element) -> None:
    for child in list(body_root):
        if child.get("id") in {"0", "1"}:
            continue
        background_root.append(copy.deepcopy(child))


def drawing_name(controller_id: str, section: str) -> str:
    return f"{controller_id} - {section}"


def main() -> None:
    a = cli()
    project_input = a.project_dir.resolve()
    # Accept either a project directory or the project points.csv path.
    # Example inputs:
    #   project/testProject
    #   project/testProject/points.csv
    if project_input.is_file() or project_input.suffix.lower() == ".csv":
        points_path = project_input
        project_dir = project_input.parent
    else:
        project_dir = project_input
        points_path = project_dir / "points.csv"
    details_path = project_dir / "panelDetails.yml"
    revisions_path = project_dir / "revHistory.csv"

    details = project_values(load_yaml(details_path))
    revisions = read_csv(revisions_path)
    if not a.no_revision_prompt and sys.stdin.isatty():
        revisions = prompt_revision(revisions_path, revisions)
    latest = latest_revision(revisions)

    points = read_csv(points_path)
    if not points:
        raise ValueError(f"No point rows found in {points_path}")
    fields = list(points[0].keys())

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in points:
        controller = (row.get("controller_id") or "UNASSIGNED").strip()
        grouped.setdefault(controller, []).append(row)

    mxfile = ET.Element("mxfile", {"host": "Electron"})
    with tempfile.TemporaryDirectory(prefix="ftdraw_") as temp_name:
        temp = Path(temp_name)
        for page_number, (controller_id, rows) in enumerate(grouped.items(), start=1):
            page_csv = temp / f"{controller_id}.csv"
            body_file = temp / f"{controller_id}.drawio"
            write_csv(page_csv, rows, fields)
            build_body(page_csv, body_file)

            _, background_graph, background_root = load_diagram(a.background)
            _, _, body_root = load_diagram(body_file)

            for key in STATIC_IDS:
                set_value(background_root, key, details.get(key, ""))
            set_value(background_root, "Drawing_Name", drawing_name(controller_id, a.section_name))
            set_latest_revision(background_root, latest)
            merge_body(background_root, body_root)

            diagram = ET.SubElement(mxfile, "diagram", {
                "name": drawing_name(controller_id, a.section_name),
                "id": f"page-{page_number}-{controller_id}",
            })
            diagram.append(copy.deepcopy(background_graph))

    ET.indent(mxfile, space="  ")
    a.output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(mxfile).write(a.output, encoding="utf-8", xml_declaration=True)
    print(f"Created {a.output} with {len(grouped)} page(s)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
